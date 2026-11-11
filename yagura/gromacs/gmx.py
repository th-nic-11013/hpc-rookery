"""Where gmx lives, its environment, and reading .edr energy terms.

DELIBERATE COPY: hyogo/gromacs/env.py (GMX, gmx_env) and
hyogo/gromacs/energy.py (energy_series) carry the same code, because yagura
and hyogo import nothing from each other. Change both together.

gmx is found from CMD_GMX, then PATH, then /usr/local/gromacs/bin/gmx.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

GMX = os.environ.get("CMD_GMX") or shutil.which("gmx") or "/usr/local/gromacs/bin/gmx"


def gmx_env():
    e = dict(os.environ)
    e["PATH"] = f"{Path(GMX).parent}:{e['PATH']}"
    e.setdefault("GMX_MAXBACKUP", "-1")
    return e


def energy_series(edr, terms, begin_ps=None, end_ps=None):
    """-> (time_ps, {term: array}); `terms` are gmx energy menu names, e.g. Temperature, Pressure, Volume, Density."""
    edr = Path(edr)
    with tempfile.TemporaryDirectory() as td:
        xvg = Path(td) / "e.xvg"
        cmd = [GMX, "energy", "-f", str(edr), "-o", str(xvg)]
        if begin_ps is not None: cmd += ["-b", str(begin_ps)]
        if end_ps is not None: cmd += ["-e", str(end_ps)]
        r = subprocess.run(cmd, input="\n".join(terms) + "\n\n", capture_output=True, text=True, env=gmx_env())
        if not xvg.exists():
            raise RuntimeError("gmx energy failed:\n" + (r.stdout + r.stderr)[-800:])
        txt = xvg.read_text().splitlines()
    legs = [l.split('"')[1] for l in txt if l.startswith("@ s") and "legend" in l]
    d = np.array([[float(x) for x in l.split()] for l in txt if l and l[0] not in "#@"])
    return d[:, 0], {n: d[:, i + 1] for i, n in enumerate(legs)}
