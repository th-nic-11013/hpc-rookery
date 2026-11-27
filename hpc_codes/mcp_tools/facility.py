"""hpc_codes MCP tools: facility.

hpc_codes/server.py loads every module in this folder. A new facility-check tool
goes here; a new group of tools is a new module beside this one.

Facility checks before a code is installed or benchmarked. Read-only: one
command on the login node that changes nothing, and local files.
"""
from hpc_codes import probe
from hpc_codes.mcp_tools import _tool


@_tool
def facility_probe(facility: str, code: str = "cp2k") -> dict:
    """What a code needs from a facility before install or benchmark, in one
    read-only login-node command plus local facts; nothing is written, no
    permission step needed.

    - python: the pack's <CODE>_PYTHON (else python3) on the login node:
      path, version, ok when >= 3.10 (the helper scripts and tools need it).
    - internet: one bounded HTTPS GET from the login node (curl, 5 s) to
      a well-known URL; http_code is the reply ("000": no route), ok when
      a reply below 400 came back. No internet is a fact, not an error:
      downloads then go through this machine (fs_upload).
    - guard: the submit guard as this server runs it (enabled: is
      HPC_SUBMIT_GUARD on) and budget_core_hours from submit_guard in the
      local ~/.hpc-agent/<slug>.json (config_path, config_present,
      budget_present; error when the value is malformed). With no budget the
      guard checks no worst case, so an unattended run should have one.
    - scheduler: from the facility's bundled facts.
    - launcher: the pack's <CODE>_LAUNCH launcher line (runs planned by
      launch_plan), bench_launcher: <CODE>_BENCH_LAUNCH (the benchmark
      scripts'); null when the facility has no such pack value.

    Returns {"facility", "code", "python": {command, source, path,
    version, ok, min}, "internet": {url, http_code, curl, ok}, "guard":
    {enabled, config_path, config_present, budget_core_hours,
    budget_present, error}, "scheduler", "launcher", "bench_launcher",
    "pack" (the facility opted into the code pack)}.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        code: code pack whose values to read (python, launcher), e.g.
            "cp2k"; a facility without that pack is probed with python3.
    """
    return probe.probe(facility, code)
