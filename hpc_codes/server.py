"""Entry point for the code-pack MCP server (hpc-codes-mcp).

Registers every facility (via importing hpc_mcp), then exposes the tools
the code-pack skills under codes/ call. Every tool takes the facility slug
first, like the scheduler tools. Records live on the machine running this
server, so none of these tools opens a connection to a cluster.
"""
import hpc_mcp  # noqa: F401 -- import for its side effect: registers every facility
from hpc_agent_core.mcp_server import MCPServer
from hpc_agent_core.serving import serve
from hpc_codes import records

mcp = MCPServer("hpc-codes-mcp")


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
def records_check(facility: str) -> dict:
    """Validate every record of a facility in both scopes, and the links
    between them (each benchmark's build_id resolves or is marked
    build_external). Returns {"counts": {type: n}, "errors": [...]}; an
    empty errors list means everything is valid.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
    """
    return records.check(facility)


def main():
    serve(mcp)


if __name__ == "__main__":
    main()
