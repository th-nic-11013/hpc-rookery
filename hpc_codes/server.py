"""Entry point for the code-pack MCP server (hpc-codes-mcp).

Registers every facility (via importing hpc_mcp), then exposes the tools
the code-pack skills under codes/ call. Every tool takes the facility slug
first, like the scheduler tools. The records_* tools work on local files
and never contact a cluster; the cp2k_* tools run one read-only command on
the facility's login node, after the same configuration check as the
scheduler tools; launch_plan reads records and pack values only.
"""
import hpc_mcp  # noqa: F401 -- import for its side effect: registers every facility
from hpc_agent_core.mcp_server import MCPServer
from hpc_agent_core.serving import serve
from hpc_codes import launch, records
from hpc_codes.cp2k import tools as cp2k

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


# ---------------------------------------------------------------------------
# CP2K. These read on the cluster's login node and change nothing there, so,
# unlike run_command_on_cluster, they need no preview-and-permission step.
# ---------------------------------------------------------------------------

@mcp.tool()
def cp2k_bench_parse(facility: str, stage_dirs: list[str]) -> dict:
    """Parse finished CP2K benchmark stage folders on the cluster (the
    runs/<jobid>/<stage>/ folders the cp2k-benchmark job writes). Read-only;
    runs on the login node, no permission step needed.

    Returns {"node_cores", "runs": [per run: input, R (ranks), T (threads),
    rep, done, sp (single point), steps, s_per_step, first_s, cp2k_s,
    grid_s, dbcsr_s, fft_s, peak_mib, dE_max, dF_max, scf_nc, scf_iters,
    ok], "stages": {folder: {found, runs, passed, ranked}}}. `passed` is
    the stage gate (every run finished, converged and agrees in energy and
    forces); `ranked` lists usable full-node layouts, fastest first.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        stage_dirs: stage folders on the cluster, absolute or under ~.
    """
    return cp2k.bench_parse(facility, stage_dirs)


@mcp.tool()
def cp2k_check_input(facility: str, input_path: str, build_id: str | None = None) -> dict:
    """Parse-check a CP2K input on the login node (`cp2k.psmp --check`,
    serial, 60 s limit) with a recorded build, from the input's own folder
    so @INCLUDE files resolve. Proves the syntax, not that the SCF
    converges. Read-only, no permission step needed.

    Returns {"ok", "build_id", "exit_code", "timed_out", "errors", "tail"}.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        input_path: the .inp file on the cluster, absolute or under ~.
        build_id: a software_registry build_id; omit for the default build.
    """
    return cp2k.check_input(facility, input_path, build_id)


@mcp.tool()
def cp2k_check_build(facility: str, build_id: str | None = None) -> dict:
    """Check that a recorded CP2K build still works on the cluster: its env
    script exists, cp2k.psmp is found after sourcing it, no shared library
    is missing (ldd), the version matches the record, and its data folder
    exists. Read-only, no permission step needed.

    Returns {"ok", "build_id", "env_script_ok", "binary", "version",
    "cp2kflags", "missing_libs", "data_dir_ok"}.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        build_id: a software_registry build_id; omit for the default build.
    """
    return cp2k.check_build(facility, build_id)


# ---------------------------------------------------------------------------
# Launch planning. Local: reads records and pack values, never the cluster.
# ---------------------------------------------------------------------------

@mcp.tool()
def launch_plan(facility: str, code: str, run_type: str, system: dict,
                method: dict | None = None, build_id: str | None = None,
                nodes: int | None = None, optimize: str = "cost",
                steps: int | None = None) -> dict:
    """How to launch a run of `code` on this facility: the build, the
    ranks x threads layout and node count, a job-spec fragment for
    submit_job, and a cost estimate, from the recorded builds and
    benchmark timings. Local only: never contacts the cluster.

    Merge `job_spec` into the submit_job spec and add name, directory,
    executable and attributes.duration (`estimate.suggested_duration`
    when present). `basis.match` says how good the timing is: "exact"
    (this system was benchmarked), "similar" (a system within 30 % in
    size; the time is rough) or "none" (a common layout and no estimate;
    benchmark first if the job is long).

    Returns {"build", "layout", "basis": {match, why, record_ids,
    options: [per node count: layout, value, core_hours_per_unit,
    efficiency]}, "job_spec", "estimate" | null, "warnings"}.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        code: code pack, e.g. "cp2k".
        run_type: aimd_step, single_point, geo_opt or cell_opt.
        system: {"atoms": int} at least; "label" (e.g. "UiO-66-conv")
            finds an exact record, "electrons" sharpens "similar".
        method: optional exact filters on the recorded method, e.g.
            {"functional": "revPBE", "hybrid": false, "cutoff_ry": 450}.
        build_id: a software_registry build_id; omit for the default build.
        nodes: fix the node count; omit to choose one.
        optimize: "cost" (fewest core-hours, the default) or "time"
            (fastest at >= 70 % parallel efficiency).
        steps: MD or optimisation steps, for the total estimate.
    """
    return launch.plan(facility, code, run_type, system, method, build_id, nodes,
                       optimize, steps)


def main():
    serve(mcp)


if __name__ == "__main__":
    main()
