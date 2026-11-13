#!/usr/bin/env python3
"""Keep rookery's copies of yagura's and hyogo's files in step with them.

yagura and hyogo are their own repositories (split 2026-11-13). Rookery
keeps three kinds of copy, refreshed from their checkouts next to this one
(or $YAGURA_ROOT / $HYOGO_ROOT):

- tool manifests: tests/fixtures/tool_manifests/<repo>.json, a copy of each
  repo's tool_manifest.json (its scripts/tool_manifest.py writes it). The
  code-pack check reads them to know which tools and arguments exist.
- skill scripts: yagura modules the structure-making skill ships as
  standalone scripts (codes/common/structure-making/scripts/).
- the MCP shim: hpc_agent_core/mcp_server.py, yagura/mcp_server.py and
  hyogo/mcp_server.py are deliberate copies from `import sys` on. Never
  synced, only compared: change all three together.

    python3 scripts/sibling_sync.py           # copy the manifests and scripts in
    python3 scripts/sibling_sync.py --check   # exit 1 if any copy differs

A sibling that is not checked out is reported and skipped (CI has none).
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (rookery path, repo, path in that repo)
COPIES = [
    ("tests/fixtures/tool_manifests/yagura.json", "yagura", "tool_manifest.json"),
    ("tests/fixtures/tool_manifests/hyogo.json", "hyogo", "tool_manifest.json"),
    ("codes/common/structure-making/scripts/geomcheck.py", "yagura", "yagura/geomcheck.py"),
    ("codes/common/structure-making/scripts/inspect_cif.py", "yagura", "yagura/inspect_cif.py"),
    ("codes/common/structure-making/scripts/to_extxyz.py", "yagura", "yagura/to_extxyz.py"),
    ("codes/common/structure-making/scripts/trajectory.py", "yagura", "yagura/analysis/trajectory.py"),
]
SHIMS = {"yagura": "yagura/mcp_server.py", "hyogo": "hyogo/mcp_server.py"}


def sibling(repo: str) -> Path | None:
    env = os.environ.get(f"{repo.upper()}_ROOT")
    path = Path(env) if env else ROOT.parent / repo
    return path if path.is_dir() else None


def shim_body(path: Path) -> str:
    text = path.read_text()
    return text[text.index("\nimport sys"):]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if any copy differs")
    a = ap.parse_args()
    missing = sorted({repo for _, repo, _ in COPIES if sibling(repo) is None})
    for repo in missing:
        print(f"no {repo} checkout next to this one: its copies not compared", file=sys.stderr)
    stale = []
    for mine, repo, theirs in COPIES:
        root = sibling(repo)
        if root is None:
            continue
        src, dst = root / theirs, ROOT / mine
        if dst.exists() and src.read_bytes() == dst.read_bytes():
            continue
        if a.check:
            stale.append(f"{mine} differs from {repo}/{theirs}")
        else:
            shutil.copyfile(src, dst)
            print(f"updated {mine} from {repo}/{theirs}")
    ours = shim_body(ROOT / "hpc_agent_core" / "mcp_server.py")
    for repo, path in SHIMS.items():
        root = sibling(repo)
        if root is not None and shim_body(root / path) != ours:
            stale.append(f"{repo}/{path} differs from hpc_agent_core/mcp_server.py "
                         "(deliberate copies: change all three together)")
    if stale:
        print("\n".join(stale))
        if a.check:
            print("run python3 scripts/sibling_sync.py (the shim is fixed by hand)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
