"""Reading .edr energy terms with gmx energy (the driver needs it for box_from).

DELIBERATE COPY of energy_series in yagura/gromacs/gmx.py: hyogo and yagura
import nothing from each other. Change both together.
"""
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from . import env


def energy_series(edr, terms, begin_ps=None, end_ps=None):
    """-> (time_ps, {term: array}); `terms` are gmx energy menu names, e.g. Temperature, Pressure, Volume, Density."""
    edr = Path(edr)
    with tempfile.TemporaryDirectory() as td:
        xvg = Path(td) / "e.xvg"
        cmd = [env.GMX, "energy", "-f", str(edr), "-o", str(xvg)]
        if begin_ps is not None: cmd += ["-b", str(begin_ps)]
        if end_ps is not None: cmd += ["-e", str(end_ps)]
        r = subprocess.run(cmd, input="\n".join(terms) + "\n\n", capture_output=True, text=True, env=env.gmx_env())
        if not xvg.exists():
            raise RuntimeError("gmx energy failed:\n" + (r.stdout + r.stderr)[-800:])
        txt = xvg.read_text().splitlines()
    legs = [l.split('"')[1] for l in txt if l.startswith("@ s") and "legend" in l]
    d = np.array([[float(x) for x in l.split()] for l in txt if l and l[0] not in "#@"])
    return d[:, 0], {n: d[:, i + 1] for i, n in enumerate(legs)}
