#!/usr/bin/env python3
"""PreToolUse hook: refuse tool calls that would flood the agent's context.

Every tool result stays in the conversation and is re-read on every later
call, so one 50 KB diff costs far more than its own size. This hook denies,
with an instruction saying what to do instead:

- Read of a text file over MAX_LINES lines without `limit`;
- Bash `git diff` / `git show` / `git log -p` with no summary flag
  (--stat, --name-only, --check, ...) and no `--` pathspec, unless piped
  into head/tail/wc/grep/...;
- Bash `cat` of files totalling over MAX_LINES lines, unless piped.

A command containing `# allow-big` passes (the user asked to see it all).
Anything this hook can't parse is allowed: it guards, it never blocks work.
Reads JSON on stdin (Claude Code hook input); prints a deny decision or
nothing.
"""
import json
import re
import shlex
import sys
from pathlib import Path

MAX_LINES = 400
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".ipynb", ".npy", ".gz"}
BOUNDED = {"head", "tail", "wc", "grep", "rg", "sed", "awk", "cut", "sort", "uniq", "less",
           "jq", "diffstat", "xargs"}
SUMMARY = {"--stat", "--shortstat", "--numstat", "--name-only", "--name-status", "--check",
           "--quiet", "--exit-code", "--summary", "--dirstat", "--compact-summary",
           "--no-patch", "-s", "--oneline"}


def _lines(path: Path) -> int:
    try:
        with path.open("rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def check_read(inp: dict) -> str | None:
    path = Path(inp.get("file_path") or "")
    if inp.get("limit") or path.suffix.lower() in BINARY or not path.is_file():
        return None
    n = _lines(path)
    if n > MAX_LINES:
        return (f"{path.name} has {n} lines (over {MAX_LINES}): read the part you need with "
                "offset/limit, or find it first with Grep / `grep -n`")
    return None


def _git_flood(tokens: list[str]) -> str | None:
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):     # git -C dir, -c k=v, --no-pager
        i += 2 if tokens[i] in ("-C", "-c") else 1
    if i >= len(tokens):
        return None
    sub, rest = tokens[i], tokens[i + 1:]
    if sub == "log" and not ({"-p", "--patch"} & set(rest)):
        return None
    if sub not in ("diff", "show", "log"):
        return None
    if "--" in rest or SUMMARY & set(rest) or any(t.startswith(("--stat=", "--dirstat=")) for t in rest):
        return None
    return (f"`git {sub}` without a summary flag prints every changed line into the context: "
            f"use `git {sub} --stat` (or --name-only), limit it to paths after `--`, or pipe it "
            "into head/grep. Add `# allow-big` only if the user asked for the full output")


def _cat_flood(tokens: list[str], cwd: Path) -> str | None:
    files = [t for t in tokens[1:] if not t.startswith("-")]
    total = sum(_lines(p if p.is_absolute() else cwd / p) for p in map(Path, files))
    if total > MAX_LINES:
        return (f"cat would print {total} lines: use Read with offset/limit, `sed -n 'a,bp'`, "
                "head/tail or grep -n")
    return None


def check_bash(cmd: str, cwd: Path) -> str | None:
    if "# allow-big" in cmd:
        return None
    # one pipeline per ;, &&, || or newline; a heredoc body is not a command
    body = re.sub(r"<<-?\s*'?(\w+)'?.*?^\1$", "", cmd, flags=re.S | re.M)
    for pipeline in re.split(r"&&|\|\||;|\n", body):
        stages = [s.strip() for s in re.split(r"(?<!\|)\|(?!\|)", pipeline) if s.strip()]
        if not stages:
            continue
        try:
            tokens = shlex.split(stages[0])
        except ValueError:
            continue
        while tokens and re.fullmatch(r"\w+=\S*", tokens[0]):       # VAR=x cmd
            tokens = tokens[1:]
        if not tokens:
            continue
        piped = len(stages) > 1 and any(
            (shlex.split(s) or [""])[0].split("/")[-1] in BOUNDED for s in stages[1:]
            if s and not s.startswith(("(", "{")))
        if piped or ">" in stages[-1]:
            continue
        prog = tokens[0].split("/")[-1]
        why = _git_flood(tokens) if prog == "git" else _cat_flood(tokens, cwd) if prog == "cat" else None
        if why:
            return why
    return None


def decide(event: dict) -> str | None:
    tool, inp = event.get("tool_name"), event.get("tool_input") or {}
    try:
        if tool == "Read":
            return check_read(inp)
        if tool == "Bash":
            return check_bash(inp.get("command") or "", Path(event.get("cwd") or "."))
    except Exception:            # never block work on a guard bug
        return None
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    why = decide(event)
    if why:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": f"output guard: {why}"}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
