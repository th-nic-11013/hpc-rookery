"""The PreToolUse output guard (scripts/hooks/output_guard.py) denies the
calls that flood an agent's context and lets everything else through.
Run: python3 tests/test_output_guard.py (scripts/check runs it)."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / "scripts" / "hooks" / "output_guard.py"


def run(event: dict) -> str | None:
    r = subprocess.run([sys.executable, "-I", str(HOOK)], input=json.dumps(event),
                       capture_output=True, text=True, check=True)
    if not r.stdout.strip():
        return None
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny", out
    return out["permissionDecisionReason"]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        big, small = d / "big.py", d / "small.py"
        big.write_text("x = 1\n" * 900)
        small.write_text("x = 1\n" * 50)
        bash = lambda c: run({"tool_name": "Bash", "tool_input": {"command": c}, "cwd": tmp})
        read = lambda **i: run({"tool_name": "Read", "tool_input": i})

        denied = {
            "read big": read(file_path=str(big)),
            "git diff": bash("git diff"),
            "git diff HEAD~1": bash("cd repo && git diff HEAD~1"),
            "git -C x diff": bash("git -C some/dir diff --cached"),
            "git show": bash("git show abc123"),
            "git log -p": bash("git log -p -3"),
            "cat big": bash("cat big.py"),
            "chained diff": bash("git status --short && git diff; echo done"),
        }
        for name, why in denied.items():
            assert why, f"{name} was allowed"
        assert "offset/limit" in denied["read big"] and "900 lines" in denied["read big"]
        assert "--stat" in denied["git diff"] and "allow-big" in denied["git diff"]

        allowed = {
            "read big with limit": read(file_path=str(big), limit=50),
            "read small": read(file_path=str(small)),
            "read missing": read(file_path=str(d / "nope.py")),
            "read image": read(file_path=str(d / "x.png")),
            "git diff --stat": bash("git diff --stat"),
            "git diff --check": bash("git diff --cached --check && echo ok"),
            "git diff path": bash("git diff -- hyogo/cp2k/e0s.py"),
            "git diff | head": bash("git diff | head -40"),
            "git diff | grep": bash("git diff | grep -n TODO"),
            "git diff to file": bash("git diff > /tmp/x.patch"),
            "git log": bash("git log --oneline -5"),
            "git show --stat": bash("git show --stat HEAD"),
            "cat small": bash("cat small.py"),
            "cat big | head": bash("cat big.py | head"),
            "allow-big": bash("git diff  # allow-big"),
            "heredoc": bash("python3 - <<'EOF'\nprint('git diff')\nEOF"),
            "unparsable": bash("echo 'unterminated"),
            "other tool": run({"tool_name": "Edit", "tool_input": {"file_path": str(big)}}),
            "bad json": None,
        }
        r = subprocess.run([sys.executable, "-I", str(HOOK)], input="not json",
                           capture_output=True, text=True)
        assert r.returncode == 0 and not r.stdout, r
        for name, why in allowed.items():
            assert why is None, f"{name} was denied: {why}"
    print(f"output guard: {len(denied)} denied, {len(allowed)} allowed, as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
