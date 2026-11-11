import json
import os
import subprocess
import sys
import tempfile
from checks._common import ROOT

LABEL = 'every server refuses unknown tool arguments'


def every_server_refuses_unknown_tool_arguments():
    """G19: the SDK's argument models ignore extra fields, so a misspelt
    argument (or one sent to a server running code from before it
    existed) used to vanish and the tool ran with the default. Each
    group's shim refuses it, naming the argument; valid calls still run;
    every server starts with an empty HOME; the check falls back to the
    SDK's behaviour (and says so on stderr) if it ever breaks; and the
    three deliberate copies of the shim stay identical."""
    root = ROOT
    fx = root / "tests" / "fixtures" / "structures" / "clean.xyz"
    probe = f"""
import asyncio, json, os, sys, tempfile
from unittest.mock import patch
import hpc_agent_core.mcp_server, yagura.mcp_server, hyogo.mcp_server
from hpc_mcp.hpc_server import mcp as hpc
from hpc_mcp.docs_server import mcp as docs
from hpc_codes.server import mcp as codes
from yagura.server import mcp as yag
from hyogo.server import mcp as hyo

def call(m, name, args):
    try:
        r = asyncio.run(m.call_tool(name, args))
    except Exception as e:
        return "ERR " + str(e)
    return "".join(c.text for c in r.content if hasattr(c, "text"))

for m in (hpc, docs, codes):
    assert isinstance(m, hpc_agent_core.mcp_server.MCPServer), m
assert isinstance(yag, yagura.mcp_server.MCPServer)
assert isinstance(hyo, hyogo.mcp_server.MCPServer)

out = tempfile.mkdtemp()
kinds = {{"O": {{"q": 6, "basis": "DZVP-MOLOPT-SR-GTH", "potential": "GTH-PBE-q6"}}}}
res = {{
    "rookery_bad": call(hpc, "get_facility", {{"facility": "hokusai", "faclity": "x"}}),
    "rookery_ok": call(hpc, "get_facility", {{"facility": "hokusai"}}),
    "codes_bad": call(codes, "launch_plan", {{"facilty": "hokusai"}}),
    "yagura_bad": call(yag, "structure_check", {{"path": {str(fx)!r}, "pth": "x"}}),
    "yagura_ok": call(yag, "structure_check", {{"path": {str(fx)!r}}}),
    "hyogo_bad": call(hyo, "cp2k_make_subsys", {{"xyz_path": {str(fx)!r}, "kinds": kinds,
                      "out_path": os.path.join(out, "a"), "symmetri": "CUBIC"}}),
    "hyogo_own": call(hyo, "cp2k_make_subsys", {{"xyz_path": {str(fx)!r}, "kinds": kinds,
                      "out_path": os.path.join(out, "b")}}),
    "hyogo_files": sorted(os.listdir(out)),
}}
with patch.object(type(yag), "list_tools", side_effect=RuntimeError("future SDK")):
    res["fallback_ok"] = call(yag, "structure_check", {{"path": {str(fx)!r}, "pth": "x"}})
print(json.dumps(res))
"""
    with tempfile.TemporaryDirectory() as home:
        out = subprocess.run([sys.executable, "-c", probe], cwd=root,
                             env={**os.environ, "HOME": home},
                             capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    res = json.loads(out.stdout.strip().splitlines()[-1])
    tail = "If the tool should have it, the server may be running old code: restart the session."
    assert res["rookery_bad"] == ("ERR get_facility got unknown argument(s) ['faclity']; "
                                  f"it takes: facility. {tail}"), res["rookery_bad"]
    assert '"scheduler": "slurm"' in res["rookery_ok"], res["rookery_ok"]
    assert res["codes_bad"].startswith("ERR launch_plan got unknown argument(s) ['facilty']"), res
    assert res["yagura_bad"] == f"ERR structure_check got unknown argument(s) ['pth']; it takes: path. {tail}"
    assert json.loads(res["yagura_ok"])["ok"] is True, res["yagura_ok"]
    assert res["hyogo_bad"].startswith(
        "ERR cp2k_make_subsys got unknown argument(s) ['symmetri']; it takes: xyz_path, kinds, "
        "out_path,") and "symmetry" in res["hyogo_bad"], res["hyogo_bad"]
    # the valid call reaches the tool, whose own refusal comes back
    assert "no kind for ['H']" in res["hyogo_own"] and "unknown argument" not in res["hyogo_own"], res
    assert res["hyogo_files"] == [], res["hyogo_files"]
    # a broken check must not break calls: the SDK default (extras ignored) applies
    assert json.loads(res["fallback_ok"])["ok"] is True, res["fallback_ok"]
    assert "unknown-argument check disabled" in out.stderr, out.stderr[-2000:]

    def body(pkg):
        text = (root / pkg / "mcp_server.py").read_text()
        return text[text.index("\nimport sys"):]
    assert body("hpc_agent_core") == body("yagura") == body("hyogo"), \
        "the three mcp_server.py shims are deliberate copies: change all three together"
