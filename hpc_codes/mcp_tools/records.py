"""hpc_codes MCP tools: records.

hpc_codes/server.py loads every module in this folder. A new records tool goes here;
a new group of tools is a new module beside this one.
"""
from hpc_codes import records
from hpc_codes.mcp_tools import _tool


@_tool
def records_query(facility: str, record_type: str, filters: dict | None = None,
                  fields: list[str] | None = None) -> list[dict]:
    """Read records of one type for a facility, from both scopes (the
    shared records shipped with the facility, and this user's own). Each
    result carries `_scope` ("facility" or "user").

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        record_type: software_registry, benchmarks or benchmarks_provenance.
        filters: exact matches on field values; dotted keys reach nested
            fields, e.g. {"code": "cp2k", "default": true} or
            {"system.label": "H2O-64", "nodes": 1}. A number or a version
            matches whether stored as text or as a number.
        fields: return only these fields (dotted paths allowed), plus the
            id and `_scope`. Omit for whole records.
    """
    return records.query(facility, record_type, filters, fields)


@_tool
def records_append(facility: str, record_type: str, record: dict) -> dict:
    """Validate one record against the schema and append it to this user's
    records. Never edits or removes a record: a correction is a new record
    whose `supersedes` names the old id. Refuses an invalid record or an id
    that already exists, saying why. Returns {"id", "scope", "path"}.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        record_type: software_registry, benchmarks or benchmarks_provenance.
        record: the record as a mapping, with every required field.
    """
    return records.append(facility, record_type, record)


@_tool
def records_check(facility: str) -> dict:
    """Validate every record of a facility in both scopes, and the links
    between them (each benchmark's build_id resolves or is marked
    build_external). Returns {"counts": {type: n}, "errors": [...]}; an
    empty errors list means everything is valid.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
    """
    return records.check(facility)
