"""Check an INCAR against VASP's own documentation by calling lit-search.

lit-search keeps the VASP wiki's INCAR tags as verified records and exports them as a
reference index; `lit-search reference check VASP INCAR --json` checks a file against it
(unknown tags, values that don't fit a tag's type or words, tags newer than a VASP
version, tags set twice). hyogo calls it as a separate program, so the two stay
independent: nothing is imported, and without lit-search the check is skipped and says so.

Where lit-search and its library are (set them in the hyogo MCP server's env):
  HYOGO_LITSEARCH          the lit-search command (default: `lit-search` on PATH)
  HYOGO_LITSEARCH_LIBRARY  the library whose litsearch.json sets references.VASP
                           (default: lit-search's own lookup: LITSEARCH_LIBRARY or the
                           working directory)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

TIMEOUT_S = 60


def check(incar_path: str | Path, vasp_version: str | None = None) -> dict:
    """{"ran": bool, "findings": [{line, tag, value, level, message}], "errors": n,
    "why": reason when it did not run}. Never raises for a missing lit-search."""
    exe = os.environ.get("HYOGO_LITSEARCH") or shutil.which("lit-search")
    if not exe:
        return {"ran": False, "findings": [], "errors": 0,
                "why": "lit-search not found (set HYOGO_LITSEARCH to its path)"}
    cmd = [exe]
    lib = os.environ.get("HYOGO_LITSEARCH_LIBRARY")
    if lib:
        cmd += ["--library", str(Path(lib).expanduser())]
    cmd += ["reference", "check", "VASP", str(Path(incar_path).expanduser()), "--json"]
    if vasp_version:
        cmd += ["--vasp-version", str(vasp_version)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ran": False, "findings": [], "errors": 0, "why": f"lit-search failed: {e}"}
    if out.returncode not in (0, 1):  # 1 = errors found, 2 = not set up
        msg = (out.stdout.strip() or out.stderr.strip()).splitlines()
        return {"ran": False, "findings": [], "errors": 0,
                "why": "lit-search: " + (msg[-1] if msg else f"exit {out.returncode}")}
    try:
        findings = json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"ran": False, "findings": [], "errors": 0,
                "why": "lit-search printed no JSON (too old? needs `reference check VASP`)"}
    return {"ran": True, "findings": findings,
            "errors": sum(f["level"] == "error" for f in findings)}


def as_warnings(result: dict, name: str) -> list[str]:
    """One warning line per finding, or one saying the check did not run."""
    if not result["ran"]:
        return [f"{name}: INCAR not checked against the VASP documentation ({result['why']})"]
    return [f"{name}:{f['line']}: {f['level']}: {f['tag']} = {f['value']}: {f['message']}"
            for f in result["findings"]]
