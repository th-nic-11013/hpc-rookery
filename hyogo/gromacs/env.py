"""Where the external programs live. Override with environment variables:
CMD_GMX (gmx), CMD_AMBER_ENV (an AmberTools env with ParmEd), CMD_MDRUN_ARGS.

DELIBERATE COPY: GMX and gmx_env are also in yagura/gromacs/gmx.py (yagura
and hyogo import nothing from each other). Change both together.
"""
import os
import shutil
import sys
from pathlib import Path

AMBER_ENV = Path(os.environ.get("CMD_AMBER_ENV", Path.home() / "miniconda3/envs/AmberTools2023"))
GMX = os.environ.get("CMD_GMX") or shutil.which("gmx") or "/usr/local/gromacs/bin/gmx"
MDRUN_ARGS = os.environ.get("CMD_MDRUN_ARGS", "-ntmpi 4 -ntomp 5 -pin on")   # 2.74 nm box limits domain decomposition
PY = sys.executable                       # needs ase + numpy (the [gromacs] extra)
AMBER_PY = str(AMBER_ENV / "bin/python")  # needs parmed
SCRIPTS = Path(__file__).parent / "scripts"

def amber_env():
    e = dict(os.environ)
    e["AMBERHOME"] = str(AMBER_ENV)
    e["PATH"] = f"{AMBER_ENV / 'bin'}:{e['PATH']}"
    return e

def gmx_env():
    e = dict(os.environ)
    e["PATH"] = f"{Path(GMX).parent}:{e['PATH']}"
    e.setdefault("GMX_MAXBACKUP", "-1")
    return e
