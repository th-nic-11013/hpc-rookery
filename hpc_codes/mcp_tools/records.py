"""hpc_codes MCP tools: records.

hpc_codes/server.py loads every module in this folder. A new records tool goes here;
a new group of tools is a new module beside this one.
"""
from hpc_codes import records, timings
from hpc_codes.mcp_tools import _tool


@_tool
def records_query(facility: str, record_type: str, filters: dict | None = None,
                  fields: list[str] | None = None) -> list[dict]:
    """Read records of one type for a facility, from both scopes (the
    shared records shipped with the facility, and this user's own). Each
    result carries `_scope` ("facility" or "user").

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        record_type: software_registry, benchmarks, benchmarks_provenance
            or campaign.
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
        record_type: software_registry, benchmarks, benchmarks_provenance
            or campaign.
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


@_tool
def records_summarise_timings(facility: str, table_path: str, like: str | None = None,
                              base: dict | None = None, frame_prefix: str | None = None,
                              date: str | None = None, tag: str | None = None,
                              min_samples: int = 10, notes: str | None = None,
                              dry_run: bool = False, nelm: int | None = None) -> dict:
    """Turn a table of production timings (one row per run, e.g. per
    labelled frame) into summary `benchmarks` records and append them to
    this user's records: one record per layout group, never one per run.
    Rows with different nodes, ranks, threads, layout (VASP: kpar, ncore,
    npar), nbands or nkpts are never pooled. Each record's `samples` holds
    n and the mean, std (n - 1), min, median and max of seconds per SCF
    step (each run's SCF loop time / its SCF steps), with the SCF step
    range and the time per run; `metric` is wall_s = mean s per SCF step x
    extra.scf_steps (the median steps per run). A group with fewer than
    min_samples runs is still recorded, with a warning. Unconverged runs
    are left out: nscf >= nelm (pass the run's SCF step limit, VASP NELM;
    a run stopped there timed an unconverged SCF), or a false `converged`
    column; without either, a warning says they can't be told apart. Every record is
    validated before any is written. Local only: no cluster command.

    Returns {"appended", "groups": [{id, n, layout, s_per_scf_step, std,
    scf_steps: [min, median, max]}], "warnings", "records",
    "check_errors"}. Read the warnings to the user.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        table_path: a local tab- or comma-separated table with a header
            row. Needs columns ranks (total), nodes, nscf (SCF steps) and
            loop (SCF loop seconds, VASP LOOP+); optional frame, threads
            (default 1), nbands, nkpts and the layout keys in lower case.
        like: a benchmarks id of the same system and method to copy code,
            version, build, partition, node_type, system and method from.
        base: those fields instead of like, or keys overriding like's
            (system and method are merged key by key).
        frame_prefix: keep only rows whose frame starts with this, e.g.
            "Ti2CO2/": a record has one system, so a table of several
            systems' frames is summarised once per system.
        date: YYYY-MM-DD for the records and ids; omit for today.
        tag: id part, e.g. "prod", when the same system is summarised twice
            on one date.
        min_samples: warn about a layout group with fewer runs than this.
        notes: appended to the generated notes.
        dry_run: build and validate the records but append nothing.
        nelm: the SCF step limit the runs used (VASP NELM): rows with
            nscf >= nelm stopped unconverged and are left out.
    """
    return timings.summarise(facility, table_path, like, base, frame_prefix, date, tag,
                             min_samples, notes, dry_run, nelm)
