import os
import subprocess
import sys
import tempfile
from checks._common import ROOT

LABEL = 'hpc-codes server starts with an empty HOME'


def codes_server_starts_with_empty_home():
    """The never-fail invariant for the second server: a brand-new user
    with no ~/.hpc-agent at all still gets all its tools listed."""
    root = ROOT
    probe = ("import asyncio; from hpc_codes.server import mcp; "
             "print(sorted(t.name for t in asyncio.run(mcp.list_tools())))")
    with tempfile.TemporaryDirectory() as home:
        env = {k: v for k, v in os.environ.items()
               if k not in ("HPC_RECORDS_USER_DIR", "HPC_RECORDS_ROOT")}
        env["HOME"] = home
        out = subprocess.run([sys.executable, "-c", probe], cwd=root, env=env,
                             capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == ("['campaign_status', 'cp2k_bench_dryrun', 'cp2k_bench_input', 'cp2k_bench_parse', "
                                  "'cp2k_bench_records', 'cp2k_check_build', 'cp2k_check_input', "
                                  "'cp2k_kinds', 'cp2k_record_from_run', 'launch_plan', 'records_append', 'records_check', "
                                  "'records_query', 'records_summarise_timings']"), out.stdout
