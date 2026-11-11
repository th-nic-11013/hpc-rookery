"""Behavioral conformance checks that run against *every* registered facility.

This is the counterweight to PORTING.md §0a's "duplication between
facilities is fine". Facility code is deliberately written out per machine
rather than collapsed into shared abstractions — so the thing that keeps
those independent implementations honest is a shared *test*, not shared
code. When your facility establishes a behavior every facility should
honor, add a check; it then applies to all of them, including ones
onboarded later, without any of them having to import each other.

Each check is one file, found automatically (add a file, edit nothing else):

- tests/checks/facility/<name>.py: runs once per facility. It defines
  LABEL and a function <name>(slug, fac, backend); FACILITIES = ("slug",)
  limits it to those facilities.
- tests/checks/<area>/<name>.py (rookery, skills, codes, hyogo, yagura):
  runs once, under "repo-wide". It defines LABEL and a function <name>().
- tests/checks/_common.py: the few helpers several checks share.

Runs entirely offline — no SSH, no scheduler, no network. Everything here
must stay that way so CI can run it. Anything needing a live cluster
belongs in tests/live_smoke.py instead.

    .venv/bin/python tests/conformance.py
    .venv/bin/python tests/conformance.py --empty-home   # as a brand-new user (CI)
    .venv/bin/python tests/conformance.py --only hyogo   # area, file name, label or slug
    .venv/bin/python tests/conformance.py --list         # every check, nothing run
"""
import _harness  # noqa: I001 -- first: this checkout on sys.path, --empty-home (G28, G29)
_harness.setup(__file__)

import argparse
import importlib
import pathlib
import sys
import traceback

import hpc_mcp  # noqa: F401 -- registers every facility
from hpc_agent_core import config
from facilities.registry import get_backend

CHECKS_DIR = pathlib.Path(__file__).resolve().parent / "checks"
FAILURES: list[str] = []


def check(label: str, fn) -> None:
    try:
        fn()
    except Exception:
        FAILURES.append(f"{label}\n{traceback.format_exc()}")
        print(f"  ✗ {label}")
    else:
        print(f"  ✓ {label}")


def discover() -> list[dict]:
    """Every check file, in path order: {area, name, label, facilities, fn}."""
    found = []
    for path in sorted(CHECKS_DIR.glob("*/*.py")):
        if path.name.startswith("_"):
            continue
        area, name = path.parent.name, path.stem
        mod = importlib.import_module(f"checks.{area}.{name}")
        fn = getattr(mod, name, None)
        assert callable(fn) and isinstance(getattr(mod, "LABEL", None), str), \
            f"{path}: needs LABEL and a function named {name}"
        found.append({"area": area, "name": name, "label": mod.LABEL,
                      "facilities": getattr(mod, "FACILITIES", None), "fn": fn})
    return found


def matches(c: dict, only: str | None, slug: str | None = None) -> bool:
    if not only:
        return True
    hay = f"{c['area']}/{c['name']} {c['label']} {slug or ''}".lower()
    return only.lower() in hay


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="run only checks whose area, file name, label or facility slug contains this")
    ap.add_argument("--list", action="store_true", help="list the checks and exit")
    a = ap.parse_args()
    sys.path.insert(0, str(CHECKS_DIR.parent))
    checks = discover()
    repo = [c for c in checks if c["area"] != "facility"]
    per_facility = [c for c in checks if c["area"] == "facility"]
    if a.list:
        for c in checks:
            print(f"{c['area']}/{c['name']}: {c['label']}"
                  + (f" [{', '.join(c['facilities'])}]" if c["facilities"] else ""))
        return 0

    ran = 0
    todo = [c for c in repo if matches(c, a.only)]
    if todo:
        print("\n=== repo-wide ===")
        for c in todo:
            check(c["label"], c["fn"])
        ran += len(todo)
    for fac in config.list_facilities():
        todo = [c for c in per_facility
                if (not c["facilities"] or fac.slug in c["facilities"]) and matches(c, a.only, fac.slug)]
        if not todo:
            continue
        slug, f = fac.slug, config.get_facility(fac.slug)
        backend = get_backend(slug)
        print(f"\n=== {slug} ({f.display_name}) ===")
        for c in todo:
            check(c["label"], lambda c=c: c["fn"](slug, f, backend))
        ran += len(todo)

    print()
    if not ran:
        print(f"no check matches {a.only!r}; --list shows them")
        return 2
    if FAILURES:
        print(f"{len(FAILURES)} conformance check(s) FAILED:\n")
        for f in FAILURES:
            print(f)
        return 1
    print(f"All conformance checks passed ({ran}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
