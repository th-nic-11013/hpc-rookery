import pathlib
import subprocess
import sys
import tempfile
from hpc_agent_core import config
from checks._common import ROOT

LABEL = 'tests import this checkout'


def tests_import_this_checkout():
    """The packages under test, and a child Python the tests start,
    load from the checkout this file is in, not from wherever the
    venv's editable install points (a worktree would otherwise test
    the main checkout's code)."""
    import hpc_codes
    root = ROOT
    for mod in (hpc_codes, config):
        assert pathlib.Path(mod.__file__).resolve().is_relative_to(root), (mod.__name__, mod.__file__)
    with tempfile.TemporaryDirectory() as elsewhere:   # not the repo: no '' on sys.path helps
        out = subprocess.run([sys.executable, "-c", "import hpc_codes; print(hpc_codes.__file__)"],
                             cwd=elsewhere, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-1000:]
    assert pathlib.Path(out.stdout.strip()).resolve().is_relative_to(root), out.stdout
