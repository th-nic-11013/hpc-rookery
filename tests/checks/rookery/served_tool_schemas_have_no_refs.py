import json
import os
import subprocess
import sys
import tempfile
from checks._common import ROOT

LABEL = 'served tool schemas have no $ref'


def served_tool_schemas_have_no_refs():
    """G89: with a `$ref` argument (JobSpec `spec`), RiVault's models sent
    the argument as a JSON string; with the definition inline they sent an
    object. The shim lists every input schema with its `$ref`s inlined and
    `$defs` dropped, keeps a recursive definition as a `$ref`, and calls
    still validate against the same models. yagura and hyogo carry copies
    of the shim (scripts/sibling_sync.py compares them)."""
    probe = """
import asyncio, json
from hpc_agent_core.mcp_server import _inline_refs
from hpc_mcp.hpc_server import mcp

tools = asyncio.run(mcp.list_tools())
spec = next(t for t in tools if t.name == "submit_job").input_schema["properties"]["spec"]
r = asyncio.run(mcp.call_tool("render_job_script", {"facility": "hokusai", "spec": {
    "name": "t", "executable": "echo hi",
    "attributes": {"queue_name": "mpc", "duration": 600, "account": "RB000000"}}}))
rec = _inline_refs({"properties": {"n": {"$ref": "#/$defs/Node"}},
                    "$defs": {"Node": {"type": "object",
                                       "properties": {"next": {"$ref": "#/$defs/Node"}}}}})
print(json.dumps({
    "with_ref": [t.name for t in tools if "$ref" in json.dumps(t.input_schema)
                 or "$defs" in t.input_schema],
    "spec_type": spec.get("type"),
    "spec_props": sorted(spec.get("properties", {})),
    "rendered": "".join(c.text for c in r.content if hasattr(c, "text")),
    "rec": rec,
}))
"""
    with tempfile.TemporaryDirectory() as home:
        out = subprocess.run([sys.executable, "-c", probe], cwd=ROOT,
                             env={**os.environ, "HOME": home},
                             capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["with_ref"] == [], res["with_ref"]
    assert res["spec_type"] == "object", res["spec_type"]
    assert {"name", "executable", "attributes", "resources"} <= set(res["spec_props"]), res
    assert "echo hi" in res["rendered"], res["rendered"][:500]
    # a recursive definition stays a pointer, and $defs is kept for it
    node = res["rec"]["properties"]["n"]
    assert node["properties"]["next"] == {"$ref": "#/$defs/Node"}, node
    assert "Node" in res["rec"]["$defs"], res["rec"]
