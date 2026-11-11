#!/usr/bin/env python3
"""Render the tools table of hyogo/INVENTORY.md from TOOL_META.

Each hyogo/mcp_tools/<group>.py carries TOOL_META, its rows of the table
(its live tools and any planned or candidate ones for that code). The rows
go between the TOOL_TABLE:START and TOOL_TABLE:END comments, in module
order; the rest of INVENTORY.md is hand-written. A gap adds or edits rows
in its own module and re-renders; it never edits the table by hand, and a
merge conflict in the table is solved by re-rendering, not by merging.

    python3 scripts/render_inventory.py           # rewrite the table
    python3 scripts/render_inventory.py --check   # exit 1 if it is stale

TOOL_META is read with ast.literal_eval, so it must be plain literals; the
modules are not imported. tests/checks/hyogo/ check that the live rows are
exactly the tools hyogo-mcp serves.
"""
import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "hyogo" / "INVENTORY.md"
TOOLS_DIR = ROOT / "hyogo" / "mcp_tools"
COLUMNS = ["method", "stage", "code", "module", "status", "source", "notes"]
BLOCK = re.compile(r"(?P<start><!-- TOOL_TABLE:START[^>]*-->\n)(?P<body>.*?)(?P<end><!-- TOOL_TABLE:END -->)", re.S)


def tool_meta(path: Path) -> dict:
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "TOOL_META":
            return ast.literal_eval(node.value)
    return {}


def row(name: str, meta: dict) -> str:
    missing = [c for c in COLUMNS if c not in meta]
    if missing:
        raise ValueError(f"TOOL_META[{name!r}] lacks {missing}")
    cells = [f"`{name}`"] + [str(meta[c]) for c in COLUMNS]
    return "|" + "|".join(f" {c} " if c else " " for c in cells) + "|"


def render() -> list[str]:
    rows, seen = [], {}
    for path in sorted(TOOLS_DIR.glob("*.py")):
        for name, meta in tool_meta(path).items():
            if name in seen:
                raise ValueError(f"{name} is in TOOL_META of both {seen[name]} and {path.name}")
            seen[name] = path.name
            rows.append(row(name, meta))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if INVENTORY.md is stale")
    a = ap.parse_args()
    text = INVENTORY.read_text()
    m = BLOCK.search(text)
    if not m:
        print(f"{INVENTORY}: no TOOL_TABLE:START/END markers")
        return 1
    new = text[:m.start("body")] + "\n".join(render()) + "\n" + text[m.end("body"):]
    if a.check:
        if new != text:
            print(f"{INVENTORY.relative_to(ROOT)} is stale: run python3 scripts/render_inventory.py")
            return 1
        return 0
    if new != text:
        INVENTORY.write_text(new)
        print(f"rendered {INVENTORY.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
