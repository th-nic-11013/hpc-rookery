"""yagura MCP tools: gromacs.

yagura/server.py loads every module in this folder. A new gromacs tool goes here;
a new group of tools is a new module beside this one.

GROMACS run analysis (a run made by hyogo's start_equilibration). Ported
from classical-md. Needs gmx on this machine (CMD_GMX).
"""
from pathlib import Path
from yagura.gromacs import analysis as gmx_analysis, gmx
from yagura.mcp_tools import _ase, mcp


def _gmx_run(workdir: str) -> Path:
    return Path(workdir).expanduser().resolve() / "run"


@mcp.tool()
def analyze_density(workdir: str, stage: str = "npt", window_ps: float = 500, last_ps: float = 2000,
                    reference_g_cm3: float = 0.0) -> dict:
    """Density/volume/pressure/temperature of an NPT stage of a GROMACS run in workdir (hyogo's
    start_equilibration) in windows, the mean +- std over its last last_ps, the drift between the two halves
    of that window (a plateau has drift well below the std), and the percent difference to reference_g_cm3
    (e.g. an experimental or literature density). Legends are read from the xvg. Local only."""
    return gmx_analysis.density_report(_gmx_run(workdir) / f"{stage}.edr", window_ps, last_ps,
                                       reference_g_cm3 or None)


@mcp.tool()
def stage_energies(workdir: str, stage: str, terms: str = "Potential,Temperature,Pressure",
                   last_ps: float = 0) -> dict:
    """Mean and std of gmx energy terms (comma-separated menu names) for a stage of a GROMACS run in
    workdir, optionally over only the last last_ps. Local only."""
    t, d = gmx.energy_series(_gmx_run(workdir) / f"{stage}.edr", [x.strip() for x in terms.split(",")])
    m = t >= (t[-1] - last_ps) if last_ps else t >= 0
    return {"length_ps": float(t[-1]),
            **{k: {"mean": round(float(v[m].mean()), 3), "std": round(float(v[m].std()), 3)}
               for k, v in d.items()}}


@mcp.tool()
def export_frames(workdir: str, stage: str = "nvt2", n_frames: int = 5, begin_ps: float = -1,
                  end_ps: float = -1, outdir: str = "") -> dict:
    """Cut n_frames evenly spaced frames (default: second half of the stage) of a GROMACS run in workdir with
    molecules made whole and write LAMMPS data files (atom_style atomic, types C F H Li N O S = pair_coeff
    * * C F H Li N O S of the finetuned MACE model) plus extxyz, for the MLMD stage. Needs ASE (the [yagura]
    extra). Local only; fs_upload the data files to a cluster."""
    frames = _ase("gromacs.frames")
    return frames.export_frames(workdir, stage, n_frames, None if begin_ps < 0 else begin_ps,
                                None if end_ps < 0 else end_ps, outdir or None)
