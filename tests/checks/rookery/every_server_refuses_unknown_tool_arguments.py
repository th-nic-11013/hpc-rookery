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
    existed) used to vanish and the tool ran with the default. The shim
    refuses it, naming the argument; valid calls still run; the server
    starts with an empty HOME; and the check falls back to the SDK's
    behaviour (and says so on stderr) if it ever breaks. yagura and hyogo
    carry deliberate copies of the shim: scripts/sibling_sync.py compares
    them when those checkouts are next to this one."""
    root = ROOT
    probe = f"""
import asyncio, json, os, sys, tempfile
from unittest.mock import patch
import hpc_agent_core.mcp_server
from hpc_mcp.hpc_server import mcp as hpc

def call(m, name, args):
    try:
        r = asyncio.run(m.call_tool(name, args))
    except Exception as e:
        return "ERR " + str(e)
    return "".join(c.text for c in r.content if hasattr(c, "text"))

assert isinstance(hpc, hpc_agent_core.mcp_server.MCPServer)

res = {{
    "rookery_bad": call(hpc, "get_facility", {{"facility": "hokusai", "faclity": "x"}}),
    "rookery_ok": call(hpc, "get_facility", {{"facility": "hokusai"}}),
    "codes_bad": call(hpc, "launch_plan", {{"facilty": "hokusai"}}),
}}
with patch.object(type(hpc), "list_tools", side_effect=RuntimeError("future SDK")):
    res["fallback_ok"] = call(hpc, "get_facility", {{"facility": "hokusai", "faclity": "x"}})
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
    # a broken check must not break calls: the SDK default (extras ignored) applies
    assert '"scheduler": "slurm"' in res["fallback_ok"], res["fallback_ok"]
    assert "unknown-argument check disabled" in out.stderr, out.stderr[-2000:]
