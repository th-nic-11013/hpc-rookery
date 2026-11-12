"""One subpackage per onboarded facility, plus registry.py.

A facility can also live outside this folder. HPC_ROOKERY_FACILITY_PATH
names further folders (os.pathsep-separated, like PATH), each laid out like
this one: <folder>/<dir>/facility.py, facility.json, data/, skill_notes/.
They are appended to this package's __path__, so `facilities.<dir>` imports
from there and hpc_mcp loads them with the in-tree facilities. That keeps a
facility private: it lives in its own repository and this checkout never
holds its files (PORTING.md §4a).

This folder always comes first, so an extra folder can't replace a public
facility; a same-named folder there is reported by hpc_mcp, not loaded.
With the variable unset, __path__ is this folder alone.
"""
import os
from pathlib import Path

#: Environment variable naming extra folders of facilities.
PATH_ENV = "HPC_ROOKERY_FACILITY_PATH"

IN_TREE = Path(__file__).resolve().parent


def extra_paths() -> list[Path]:
    """The folders HPC_ROOKERY_FACILITY_PATH names, in order, resolved,
    without repeats and without this folder itself. A folder that does not
    exist is still returned, so hpc_mcp can report it."""
    found: list[Path] = []
    for entry in os.environ.get(PATH_ENV, "").split(os.pathsep):
        if not entry.strip():
            continue
        path = Path(entry).expanduser().resolve()
        if path != IN_TREE and path not in found:
            found.append(path)
    return found


__path__.extend(str(p) for p in extra_paths() if str(p) not in __path__)  # noqa: F821
