import json
import os
import subprocess
import sys
import tempfile
from checks._common import CODES_TOOLS, DOCS_TOOLS, ROOT

LABEL = 'hpc server serves the docs and code-pack tools'


def hpc_server_serves_docs_and_codes_tools():
    """hpc-mcp took over the docs and code-pack tools: with an empty
    HOME it lists every one of them next to the scheduler tools, and no
    tool group is reported failed. It also serves every tool the
    hpc_codes tool registry holds, so a code tool decorated with
    @mcp.tool() instead of @_tool (which register() would not see) fails
    here instead of going missing from hpc."""
    root = ROOT
    probe = ("import asyncio, json; from hpc_mcp.hpc_server import mcp, FAILED_TOOL_GROUPS; "
             "from hpc_codes.server import mcp as codes; "
             "print(json.dumps([sorted(t.name for t in asyncio.run(mcp.list_tools())), FAILED_TOOL_GROUPS, "
             "sorted(t.name for t in asyncio.run(codes.list_tools()))]))")
    with tempfile.TemporaryDirectory() as home:
        env = {k: v for k, v in os.environ.items()
               if k not in ("HPC_RECORDS_USER_DIR", "HPC_RECORDS_ROOT")}
        env["HOME"] = home
        out = subprocess.run([sys.executable, "-c", probe], cwd=root, env=env,
                             capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    names, failed, codes_names = json.loads(out.stdout.strip().splitlines()[-1])
    assert failed == {}, failed
    assert not set(codes_names) - set(names), \
        f"hpc-codes tools not on hpc (use @_tool): {sorted(set(codes_names) - set(names))}"
    missing = sorted(set(CODES_TOOLS + DOCS_TOOLS + ["submit_job", "get_facilities"]) - set(names))
    assert not missing, missing
    assert len(names) == len(set(names))
