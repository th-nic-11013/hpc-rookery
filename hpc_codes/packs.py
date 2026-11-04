"""What every code tool needs first: a facility's pack values and a build.

Pack values come from facilities/<dir>/codes/<code>/values.json, read at
call time; the build comes from software_registry.
"""
import json

from hpc_agent_core import config
from hpc_codes import records


def pack_values(facility: str, code: str) -> dict:
    path = config.get_facility(facility).data_dir.parent / "codes" / code / "values.json"
    if not path.exists():
        raise ValueError(f"{facility} has not opted into the {code} code pack "
                         f"(no codes/{code}/values.json), so these tools don't apply there.")
    return json.loads(path.read_text())


def resolve_build(facility: str, code: str, build_id: str | None = None) -> dict:
    """The software_registry record to use: the named one, else the default."""
    filters = {"code": code, **({"build_id": build_id} if build_id else {"default": True})}
    hits = [r for r in records.query(facility, "software_registry", filters) if not r.get("removed")]
    if not hits:
        known = [r["build_id"] for r in records.query(facility, "software_registry", {"code": code})
                 if not r.get("removed")]
        what = f"build {build_id!r}" if build_id else f"default {code} build"
        raise ValueError(f"No {what} in {facility}'s software_registry (removed builds don't "
                         f"count). Available: {', '.join(known) or 'none'}. "
                         f"See {facility}-{code}-build.")
    return hits[0]
