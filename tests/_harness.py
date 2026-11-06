"""Shared start-up for the offline test scripts (conformance, notebook_client).

Imported before any repo package, it does two things:

- **G28: test this checkout.** The venv's editable install points at one
  checkout. Run from another (a git worktree), `import hpc_codes` would
  silently load that other checkout's code. The repo root containing this
  file goes first on sys.path, and on PYTHONPATH so the MCP servers and
  `python -c` probes the tests start import it too.
- **G29: `--empty-home`.** Some behaviour only shows with an empty
  ~/.hpc-agent/ (a new user, CI). `--empty-home` re-runs the same script in
  a child process whose HOME is a fresh temporary folder, removed after;
  the exit code is the child's. Use it instead of `HOME=$(mktemp -d) ...`,
  which agent sandboxes refuse.
"""
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def setup(script: str) -> None:
    """Call first, as `_harness.setup(__file__)`. May exit (for --empty-home)."""
    root = str(ROOT)
    if sys.path[:1] != [root]:
        sys.path.insert(0, root)
    paths = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p and p != root]
    os.environ["PYTHONPATH"] = os.pathsep.join([root, *paths])

    if "--empty-home" in sys.argv[1:]:
        rest = [a for a in sys.argv[1:] if a != "--empty-home"]
        with tempfile.TemporaryDirectory(prefix="empty-home-") as home:
            print(f"[--empty-home] HOME={home}", file=sys.stderr)
            code = subprocess.call([sys.executable, script, *rest],
                                   env={**os.environ, "HOME": home})
        sys.exit(code)
