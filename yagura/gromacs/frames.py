"""Cut whole-molecule frames from a trajectory and write LAMMPS data files for MACE/ML-IAP.

Needs ASE (the [yagura] extra); the server imports this module inside the tool."""
import re
import subprocess
from pathlib import Path

import numpy as np
from ase.io import read, write

from . import gmx

SPECORDER = ["C", "F", "H", "Li", "N", "O", "S"]   # = pair_coeff * * C F H Li N O S of the finetuned model

def export_frames(workdir, stage="nvt2", n_frames=5, begin_ps=None, end_ps=None, outdir=None):
    wd = Path(workdir).expanduser().resolve()
    run = wd / "run"
    out = Path(outdir).expanduser().resolve() if outdir else wd / "mlmd_frames"
    out.mkdir(parents=True, exist_ok=True)
    xtc, tpr = run / f"{stage}.xtc", run / f"{stage}.tpr"
    # last time in trajectory
    r = subprocess.run([gmx.GMX, "check", "-f", str(xtc)], capture_output=True, text=True, env=gmx.gmx_env())
    tend = float(re.search(r"Last frame\s+\d+\s+time\s+([\d.]+)", r.stdout + r.stderr).group(1))
    b = begin_ps if begin_ps is not None else tend / 2
    e = end_ps if end_ps is not None else tend
    times = np.linspace(b, e, n_frames)
    ref = read(str(wd / "build/system_replicated.pdb"))     # same atom order as the topology
    symbols = ref.get_chemical_symbols()
    files = []
    for k, t in enumerate(times):
        gro = out / f"frame_{k:02d}_{t:.0f}ps.gro"
        subprocess.run([gmx.GMX, "trjconv", "-f", str(xtc), "-s", str(tpr), "-pbc", "mol", "-dump", f"{t}", "-o", str(gro)],
                       input="0\n", capture_output=True, text=True, env=gmx.gmx_env(), check=True)
        g = read(str(gro))
        assert len(g) == len(symbols), "atom count differs from system_replicated.pdb"
        g.set_chemical_symbols(symbols)
        g.set_pbc(True)
        data = out / f"frame_{k:02d}_{t:.0f}ps.data"
        write(str(data), g, format="lammps-data", specorder=SPECORDER, atom_style="atomic", masses=True)
        write(str(data.with_suffix(".extxyz")), g)
        files.append({"time_ps": float(t), "data": str(data), "box_A": [float(x) for x in g.cell.lengths()]})
    return {"stage": stage, "frames": files, "specorder": SPECORDER, "outdir": str(out)}
