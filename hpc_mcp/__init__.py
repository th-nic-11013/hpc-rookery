"""Importing this package registers every onboarded facility.

Every directory under facilities/ containing a facility.py is imported
(triggering its config.register_facility() + registry.register_backend()
calls) — a new facility PR that adds facilities/<slug>/facility.py needs no
edit here to be picked up; that's deliberate, so parallel facility PRs don't
conflict on this file. Every hpc_mcp entry point (hpc_server, docs_server,
doctor, ingest) imports hpc_mcp first (even just for this side effect)
before doing anything that needs a registered facility. The folders named
by HPC_ROOKERY_FACILITY_PATH are searched the same way, after facilities/
(see facilities/__init__.py); FACILITY_DIRS and EXTRA_FACILITIES record
where each facility came from.

**A facility that fails to import must not take the server down with it.**
This is the "never fail to start" invariant (PORTING.md §10) applied at the
place the hub made it sharper: one process now serves every facility, so an
exception in one facility.py — a typo, a bad register_facility argument, a
missing data file — would otherwise deny every *other* facility to every
user. Such a facility is skipped, its error recorded in FAILED_FACILITIES,
and the server starts with the rest. It is never skipped silently:
get_facilities() reports it, and the doctor fails on it.
"""
import importlib
import os
import pkgutil
from pathlib import Path

import facilities
from hpc_agent_core import config

#: slug -> the error that stopped it loading. Populated at import time;
#: read by hpc_server.get_facilities() and hpc_agent_core.doctor so a
#: facility can never disappear without explanation. A problem with a
#: HPC_ROOKERY_FACILITY_PATH folder itself is keyed by that folder's path.
FAILED_FACILITIES: dict[str, str] = {}

#: slug -> the folder its facility.py was loaded from.
FACILITY_DIRS: dict[str, Path] = {}

#: Slugs loaded from a HPC_ROOKERY_FACILITY_PATH folder rather than from
#: facilities/ in this checkout (PORTING.md §4a). They are not published:
#: the generated tables, plugins and marketplaces leave them out, and the
#: checks of those files skip them.
EXTRA_FACILITIES: list[str] = []

_loaded_from: dict[str, str] = {}  # folder name -> the __path__ entry it was loaded from
for _entry in facilities.__path__:
    _extra = Path(_entry).resolve() != facilities.IN_TREE
    if _extra and not os.path.isdir(_entry):
        FAILED_FACILITIES[_entry] = (f"{facilities.PATH_ENV} names {_entry}, "
                                     "which is not a folder")
        continue
    for _mod in pkgutil.iter_modules([_entry]):
        if not _mod.ispkg:
            continue  # skip registry.py itself — only facility subpackages
        if _mod.name in _loaded_from:
            # facilities.<name> already imports from an earlier folder
            # (this checkout's own comes first), so this one can't load.
            _shadowed = os.path.join(_entry, _mod.name)
            if os.path.isfile(os.path.join(_shadowed, "facility.py")):
                FAILED_FACILITIES[_shadowed] = (
                    f"not loaded: a facility folder named {_mod.name!r} already comes "
                    f"from {_loaded_from[_mod.name]}; rename this one")
            continue
        _loaded_from[_mod.name] = _entry
        _before = {f.slug for f in config.list_facilities()}
        try:
            importlib.import_module(f"facilities.{_mod.name}.facility")
        except ModuleNotFoundError as exc:
            # A subdirectory with no facility.py at all (e.g. __pycache__) is
            # not a facility and not an error. But a facility.py that exists
            # and imports something missing is a real failure, so only the
            # former is skipped quietly.
            if exc.name in (f"facilities.{_mod.name}.facility", f"facilities.{_mod.name}"):
                continue
            FAILED_FACILITIES[_mod.name] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 -- deliberate: see module docstring
            FAILED_FACILITIES[_mod.name] = f"{type(exc).__name__}: {exc}"
        finally:
            for _slug in [f.slug for f in config.list_facilities() if f.slug not in _before]:
                FACILITY_DIRS[_slug] = Path(_entry) / _mod.name
                if _extra:
                    EXTRA_FACILITIES.append(_slug)
