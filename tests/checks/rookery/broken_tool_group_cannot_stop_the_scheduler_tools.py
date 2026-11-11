import json
import os
import subprocess
import sys
import tempfile
from checks._common import CODES_TOOLS, DOCS_TOOLS, ROOT

LABEL = 'a broken tool group cannot stop the scheduler tools'


def broken_tool_group_cannot_stop_the_scheduler_tools():
    """The promise the separate hpc-codes server used to keep: a code
    tool group that fails to import is reported (stderr and
    FAILED_TOOL_GROUPS), and every scheduler and docs tool still loads."""
    root = ROOT
    probe = ("import asyncio, json, sys; sys.modules['hpc_codes.server'] = None; "
             "from hpc_mcp.hpc_server import mcp, FAILED_TOOL_GROUPS; "
             "print(json.dumps([sorted(t.name for t in asyncio.run(mcp.list_tools())), FAILED_TOOL_GROUPS]))")
    with tempfile.TemporaryDirectory() as home:
        out = subprocess.run([sys.executable, "-c", probe], cwd=root,
                             env={**os.environ, "HOME": home},
                             capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    names, failed = json.loads(out.stdout.strip().splitlines()[-1])
    assert list(failed) == ["codes"], failed
    assert "the codes tools failed to load" in out.stderr, out.stderr[-1000:]
    assert not set(CODES_TOOLS) & set(names)
    assert {"submit_job", "fs_ls", "get_facilities", *DOCS_TOOLS} <= set(names)
