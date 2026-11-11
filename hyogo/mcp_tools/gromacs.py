"""hyogo MCP tools: gromacs.

hyogo/server.py loads every module in this folder. A new gromacs tool goes here;
a new group of tools is a new module beside this one.

GROMACS (GAFF) setup and equilibration of salt/solvent boxes, on this
machine. Ported from classical-md; analysis is in yagura.
"""
import json
import os
import re
import signal
import subprocess
from pathlib import Path
from hyogo.gromacs import build as gmx_build, env as gmx_env, mdp as gmx_mdp
from hyogo.mcp_tools import mcp


_REPO = str(Path(__file__).resolve().parents[2])


def _wd(p):
    return Path(p).expanduser().resolve()


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@mcp.tool()
def build_system(workdir: str, labels_extxyz: str, charge_scale: float = 0.8, replicate: int = 2,
                 frame: str = "median", energy_key: str = "REF_energy") -> dict:
    """Build a GAFF/GROMACS box of LiTFSI in methanol from one frame of an extxyz file.
    Steps: pick frame (median-energy by default) -> make molecules whole, canonical atom order -> replicate
    NxNxN -> AM1-BCC charges (antechamber) -> symmetrise TFSI/MeOH charges -> scale TFSI and Li+ charges by
    charge_scale (MeOH unscaled) -> parmchk2 + tleap (Joung-Cheatham Li+) -> ParmEd to GROMACS top/gro.
    Needs a cubic cell with equal numbers of Li and TFSI, AmberTools (CMD_AMBER_ENV) and ASE in this
    server's Python. Local only. Writes workdir/build/system.{top,gro}; takes ~1 min."""
    return gmx_build.build_system(workdir, labels_extxyz, charge_scale, replicate, frame, energy_key)


@mcp.tool()
def start_equilibration(workdir: str, nvt1_ps: float = 1000, npt_ps: float = 1000, nvt2_ps: float = 10000,
                        temperature_K: float = 300, pressure_bar: float = 1.0, avg_last_ps: float = 500,
                        protocol_json: str = "") -> dict:
    """Start (detached, resumable) the equilibration on this machine: em -> nvt0 100 ps/1 fs with generated
    velocities -> nvt1 -> npt (C-rescale, tau-p 5) -> nvt2 at the box averaged over the last avg_last_ps of the NPT.
    Set nvt2_ps=0 to stop after the NPT. For a custom protocol pass protocol_json: a list of stages
    {name, kind: em|nvt|npt, time_ps, dt_fs, gen_vel, continuation, ref_t, ref_p, box_from: {stage, last_ps}}.
    Re-running with the same workdir resumes after the last finished stage. Poll with job_status.
    This runs gmx locally (CMD_MDRUN_ARGS sets threading); for a cluster job use the hpc server's submit_job."""
    wd = _wd(workdir)
    if not (wd / "build/system.top").exists():
        return {"error": "run build_system first (no build/system.top)"}
    st = wd / "status.json"
    if st.exists():
        s = json.loads(st.read_text())
        if s.get("state") == "running" and _alive(s["pid"]):
            return {"error": f"already running (pid {s['pid']}, stage {s.get('stage')})"}
    prot = json.loads(protocol_json) if protocol_json else gmx_mdp.default_protocol(
        nvt1_ps, npt_ps, nvt2_ps, temperature_K, pressure_bar, avg_last_ps)
    (wd / "protocol.json").write_text(json.dumps(prot, indent=1))
    e = gmx_env.gmx_env()
    e["PYTHONPATH"] = _REPO + os.pathsep + e.get("PYTHONPATH", "")
    with open(wd / "driver.log", "a") as log:
        p = subprocess.Popen([gmx_env.PY, "-m", "hyogo.gromacs.driver", str(wd)], env=e, stdout=log,
                             stderr=log, start_new_session=True, cwd=str(wd))
    return {"started": True, "driver_pid": p.pid, "stages": [s["name"] for s in prot],
            "mdrun_args": gmx_env.MDRUN_ARGS}


@mcp.tool()
def job_status(workdir: str) -> dict:
    """State of the local equilibration started in workdir by start_equilibration: running/finished/failed,
    stage, finished stages, current MD step. (Cluster jobs: the hpc server's get_job_status.)"""
    wd = _wd(workdir)
    f = wd / "status.json"
    if not f.exists():
        return {"state": "not started"}
    s = json.loads(f.read_text())
    if s["state"] == "running" and not _alive(s["pid"]):
        s["state"] = "dead (driver exited without finishing; re-run start_equilibration to resume)"
    if s.get("stage"):
        lg = wd / "run" / f"mdrun_{s['stage']}.log"
        if lg.exists():
            tail = lg.read_bytes()[-4000:].decode(errors="ignore")
            m = re.findall(r"step (\d+)(?:, will finish ([^\n\r]+))?", tail)
            if m:
                s["current_step"], s["eta"] = m[-1][0], m[-1][1] or None
        tp = wd / "run" / f"{s['stage']}.mdp"
        if tp.exists():
            n = re.search(r"nsteps\s*=\s*(\d+)", tp.read_text())
            if n:
                s["stage_nsteps"] = int(n.group(1))
    return s


@mcp.tool()
def stop_job(workdir: str) -> dict:
    """Stop the local equilibration in workdir (driver and mdrun). The finished stages are kept;
    start_equilibration resumes. (Cluster jobs: the hpc server's cancel_job.)"""
    f = _wd(workdir) / "status.json"
    if not f.exists():
        return {"error": "no status.json"}
    pid = json.loads(f.read_text())["pid"]
    if not _alive(pid):
        return {"state": "not running"}
    os.killpg(os.getpgid(pid), signal.SIGTERM)
    return {"stopped": pid}


@mcp.tool()
def system_info(workdir: str) -> dict:
    """Composition, molality, box and density of the built (or latest) structure in workdir."""
    wd = _wd(workdir)
    info = json.loads((wd / "build_info.json").read_text()) if (wd / "build_info.json").exists() else {}
    masses = {"TFS": 287.09, "MOH": 32.042, "LI": 6.94}
    m = info.get("molecules")
    if m:
        gro = wd / "run" / "npt.gro" if (wd / "run/npt.gro").exists() else wd / "build/system.gro"
        L = float(gro.read_text().split()[-3])
        mass = sum(masses[k] * v for k, v in m.items())
        info["latest_gro"] = str(gro)
        info["latest_box_nm"] = L
        info["latest_density_g_cm3"] = round(mass / 0.602214076 / L ** 3 / 1000, 4)
    return info
