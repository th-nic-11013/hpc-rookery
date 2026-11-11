"""Runs a protocol (list of stages) with GROMACS, resumable, writing status.json. Run detached:
python -m hyogo.gromacs.driver WORKDIR   (reads WORKDIR/protocol.json, uses WORKDIR/build/system.{top,gro})"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import env, mdp
from .energy import energy_series

def sh(cmd, cwd, log):
    with open(cwd / log, "w") as f:
        r = subprocess.run(cmd, cwd=cwd, env=env.gmx_env(), stdout=f, stderr=subprocess.STDOUT)
    if r.returncode:
        raise RuntimeError(f"{' '.join(map(str, cmd))} failed, see {cwd / log}")

def finished(run: Path, name):
    lg = run / f"{name}.log"
    return (run / f"{name}.gro").exists() and lg.exists() and "Finished mdrun" in lg.read_text()

def main(wd):
    wd = Path(wd).resolve()
    run = wd / "run"; run.mkdir(exist_ok=True)
    top = wd / "build/system.top"
    prot = json.loads((wd / "protocol.json").read_text())
    status = {"pid": os.getpid(), "state": "running", "stage": None, "done": [], "started": time.ctime()}
    def save():
        (wd / "status.json").write_text(json.dumps(status, indent=1))
    prev_gro, prev_cpt = wd / "build/system.gro", None
    mdr = env.MDRUN_ARGS.split()
    try:
        for st in prot:
            name = st["name"]
            status["stage"] = name; save()
            if finished(run, name):
                prev_gro, prev_cpt = run / f"{name}.gro", (run / f"{name}.cpt") if (run / f"{name}.cpt").exists() else None
                status["done"].append(name); continue
            start_gro, use_cpt = prev_gro, prev_cpt
            if "box_from" in st:
                src, last = st["box_from"]["stage"], st["box_from"]["last_ps"]
                t, d = energy_series(run / f"{src}.edr", ["Volume", "Density"], begin_ps=None, end_ps=None)
                tmax = t[-1]
                m = t >= tmax - last
                L1 = float(d["Volume"][m].mean() ** (1 / 3))
                L0 = float(Path(run / f"{src}.gro").read_text().split()[-3])
                sc = L1 / L0
                sh([env.GMX, "editconf", "-f", f"{src}.gro", "-scale", f"{sc}", f"{sc}", f"{sc}", "-o", f"{name}_start.gro"], run, f"editconf_{name}.log")
                (run / f"{name}_box.txt").write_text(f"{L1:.6f} nm  density {float(d['Density'][m].mean()):.3f} kg/m3 (last {last} ps of {src})\n")
                start_gro, use_cpt = run / f"{name}_start.gro", None
            (run / f"{name}.mdp").write_text(mdp.render(st))
            g = [env.GMX, "grompp", "-f", f"{name}.mdp", "-c", str(start_gro), "-p", str(top), "-o", f"{name}.tpr", "-maxwarn", "1"]
            if use_cpt and st.get("continuation", True) and st["kind"] != "em":
                g += ["-t", str(use_cpt)]
            sh(g, run, f"grompp_{name}.log")
            sh([env.GMX, "mdrun", *mdr, "-deffnm", name], run, f"mdrun_{name}.log")
            prev_gro, prev_cpt = run / f"{name}.gro", (run / f"{name}.cpt") if (run / f"{name}.cpt").exists() else None
            status["done"].append(name); save()
        status.update(state="finished", stage=None)
    except Exception as e:
        status.update(state="failed", error=str(e))
    status["ended"] = time.ctime(); save()

if __name__ == "__main__":
    main(sys.argv[1])
