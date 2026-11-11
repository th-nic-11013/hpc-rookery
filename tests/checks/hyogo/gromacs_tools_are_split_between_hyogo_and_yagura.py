import json
import os
import subprocess
import sys
import tempfile
from checks._common import ROOT

LABEL = 'GROMACS tools are split between hyogo and yagura'


def gromacs_tools_are_split_between_hyogo_and_yagura():
    """classical-md's tools: setup and running in hyogo, analysis in
    yagura; both start with an empty HOME; the default protocol renders;
    and the deliberate copies of the gmx helpers stay identical."""
    root = ROOT
    probe = ("import asyncio, json; from hyogo.server import mcp as h; from yagura.server import mcp as y; "
             "print(json.dumps([sorted(t.name for t in asyncio.run(m.list_tools())) for m in (h, y)]))")
    with tempfile.TemporaryDirectory() as home:
        out = subprocess.run([sys.executable, "-c", probe], cwd=root,
                             env={**os.environ, "HOME": home},
                             capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    hyo, yag = json.loads(out.stdout.strip().splitlines()[-1])
    assert {"build_system", "system_info", "start_equilibration", "job_status", "stop_job"} <= set(hyo), hyo
    assert {"analyze_density", "stage_energies", "export_frames"} <= set(yag), yag
    assert not {"analyze_density", "stage_energies", "export_frames"} & set(hyo)
    assert not {"build_system", "start_equilibration"} & set(yag)

    from hyogo.gromacs import mdp
    stages = mdp.default_protocol(nvt1_ps=10, npt_ps=10, nvt2_ps=10)
    assert [st["name"] for st in stages] == ["em", "nvt0", "nvt1", "npt", "nvt2"]
    npt = mdp.render(stages[3])
    assert "pcoupl    = C-rescale" in npt and "nsteps    = 5000" in npt, npt

    def fn(path, name):
        text = (root / path).read_text()
        start = text.index(f"def {name}(")
        nxt = text.find("\ndef ", start + 1)
        return text[start:nxt if nxt != -1 else None].strip()
    assert fn("hyogo/gromacs/energy.py", "energy_series").replace("env.", "") == \
        fn("yagura/gromacs/gmx.py", "energy_series"), "energy_series copies differ"
    assert fn("hyogo/gromacs/env.py", "gmx_env") == fn("yagura/gromacs/gmx.py", "gmx_env"), \
        "gmx_env copies differ"
