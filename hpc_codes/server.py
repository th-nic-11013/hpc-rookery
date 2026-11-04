"""Entry point for the code-pack MCP server (hpc-codes-mcp).

Registers every facility (via importing hpc_mcp), then exposes the tools
the code-pack skills under codes/ call. Every tool takes the facility slug
first, like the scheduler tools. The records_* tools work on local files
and never contact a cluster; the cp2k_* tools run one read-only command on
the facility's login node, after the same configuration check as the
scheduler tools (cp2k_bench_input then writes its inputs on this
machine); launch_plan reads records and pack values only.
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
# CP2K. These read on the cluster's login node and change nothing there (the
# dry run writes only a scratch folder of its own, and removes it), so, unlike
# run_command_on_cluster, they need no preview-and-permission step.
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


@mcp.tool()
def cp2k_kinds(facility: str, elements: list[str], basis: str | None = None,
               build_id: str | None = None, family: str = "molopt",
               aux_basis: str | None = None) -> dict:
    """The CP2K &KIND facts of a recorded build for each element: valence
    q, basis set and GTH potential, read from the build's own data folder
    on the login node, so q is never guessed. Read-only, no permission
    step needed. Pass the result to the input writers (cp2k_make_subsys,
    then cp2k_make_input, which reads `files`).

    Pick `family` by functional: "molopt" (BASIS_MOLOPT + GTH_POTENTIALS,
    GTH-PBE) for revPBE/PBE as benchmarked; "uzh-pbe", "uzh-scan" (r2SCAN,
    r2SCAN0) or "uzh-pbe0" (PBE0, HSE06) for the newest BASIS_MOLOPT_UZH* and
    POTENTIAL_UZH* of the build. Hybrids also need `aux_basis` (e.g.
    "admm-dzp"), read from BASIS_ADMM_UZH.

    Returns {"build_id", "data_dir", "files": {"basis": [file, ...],
    "potential": file}, "kinds": {element: {q, basis, potential[,
    aux_basis]}}, "errors": {element: why}}; an element with no such basis,
    potential or aux basis is in `errors`, not `kinds`.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        elements: element symbols, e.g. ["Zr", "O", "C", "H"].
        basis: basis set name without -qN; omit for the family's DZVP
            (DZVP-MOLOPT-SR-GTH for molopt, DZVP-MOLOPT-SCAN-GTH, ...).
        build_id: a software_registry build_id; omit for the default build.
        family: "molopt", "uzh-pbe", "uzh-scan" or "uzh-pbe0".
        aux_basis: ADMM auxiliary basis for hybrids, e.g. "admm-dzp"; UZH
            families only.
    """
    return cp2k.kinds(facility, elements, basis, build_id, family, aux_basis)


@mcp.tool()
def cp2k_bench_input(facility: str, xyz_path: str, name: str, out_dir: str,
                     build_id: str | None = None, steps: int = 10, smoke_steps: int = 3,
                     xc: str = "revPBE", d3: str = "zero", cutoff: int = 450,
                     rel_cutoff: int = 60, basis: str = "DZVP-MOLOPT-SR-GTH",
                     charge: int = 0, overwrite: bool = False) -> dict:
    """Write a CP2K NVE AIMD benchmark input (NAME.inp, `steps` MD steps)
    and a smoke version (NAME-smoke.inp, `smoke_steps`) from an extended
    xyz on this machine, coordinates inline. revPBE-D3, 450/60 Ry, Gamma
    point, NVE 0.5 fs from 300 K, OT/DIIS with FULL_SINGLE_INVERSE,
    IGNORE_CONVERGENCE_FAILURE. Reads each element's valence and GTH-PBE
    potential from the build's data folder on the login node (as
    cp2k_kinds), read-only; writes only on this machine, so fs_upload the
    inputs afterwards.

    Before it: structure_check the xyz (and structure_stretch_xh on
    `short_xh`, e.g. X-ray C-H bonds).

    Returns {"inputs", "build_id", "formula", "natoms", "electrons",
    "widths", "kinds": {element: potential}, "warnings"}; read the
    warnings to the user (odd electron count, cell thinner than 10 A).

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        xyz_path: extended xyz (Lattice= on line 2) on this machine.
        name: project and file name, e.g. "UiO66-prim".
        out_dir: local folder for the two inputs; created if missing.
        build_id: a software_registry build_id; omit for the default build.
        steps: MD steps of the benchmark input.
        smoke_steps: MD steps of the smoke input.
        xc: "revPBE" or "PBE".
        d3: "zero", "bj" or "none".
        cutoff: MGRID CUTOFF in Ry.
        rel_cutoff: MGRID REL_CUTOFF in Ry.
        basis: basis set name in BASIS_MOLOPT, for every element.
        charge: total charge.
        overwrite: replace the inputs if they exist.
    """
    return cp2k.bench_input(facility, xyz_path, name, out_dir, build_id, steps, smoke_steps,
                            xc, d3, cutoff, rel_cutoff, basis, charge, overwrite)


@mcp.tool()
def cp2k_bench_dryrun(facility: str, bench_dir: str, ref_out: str,
                      settings: dict | None = None, cases: list[str] | None = None,
                      fail_layout: str | None = None, time_limit_s: int = 60,
                      job_script: str | None = None, nodes: int = 4,
                      keep_scratch: bool = False) -> dict:
    """Dry-run a cp2k-benchmark folder on the login node with its own
    dryrun.sh: the stage scripts run as the job would, but a stub `srun`
    copies `ref_out` instead of starting CP2K, and the script refuses to
    run unless that stub is first on PATH. Tests the stage logic, not
    CP2K: no job, no compute. bench_dir is only read; each case runs in a
    new scratch folder under ~/agent/work/cp2k-dryrun/, removed afterwards
    unless keep_scratch. No permission step needed.

    Cases (all three by default): "pass" (must pass), "fail_layout" (the
    stub fails one layout; the job must stop there) and "time_limit"
    (BENCH_LIMIT_S=time_limit_s; the guard must refuse a run).

    Returns {"ok" (every case behaved as expected), "bench_dir",
    "job_script", "cases": [per case: case, exit_code, expected, why,
    stages_run, failed, not_started, fail_layout, scratch, log_tail]}.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        bench_dir: the benchmark folder on the cluster (dryrun.sh, the stage
            scripts, inputs/), absolute or under ~.
        ref_out: a finished cp2k.out on the cluster, e.g. the build's
            H2O-32 test output.
        settings: the job's settings as environment variables, e.g.
            {"SMOKE_INPUT": "x-smoke.inp", "SCAN_INPUT": "x.inp",
            "VALIDATE_INPUT": "none", "SCAN_LAYOUTS": "28x4 8x14"}.
        cases: a subset of ["pass", "fail_layout", "time_limit"].
        fail_layout: the layout the stub fails, e.g. "8x14" (per node for a
            job_script); omit for the last scan layout not in the smoke
            stage (for a job_script, the second of LAYOUTS).
        time_limit_s: BENCH_LIMIT_S for the "time_limit" case.
        job_script: run this job script in bench_dir instead of the three
            stages, e.g. "multinode_layouts_job.sh" (set LAYOUTS, REF_DIR,
            REF_LAYOUT in settings).
        nodes: node count the job script sees (job_script only).
        keep_scratch: keep the scratch folders to inspect; their paths are
            returned.
    """
    return cp2k.bench_dryrun(facility, bench_dir, ref_out, settings, cases, fail_layout,
                             time_limit_s, job_script, nodes, keep_scratch)


@mcp.tool()
def cp2k_bench_records(facility: str, system: dict, method: dict, run_type: str,
                       parsed: dict | None = None, stage_dirs: list[str] | None = None,
                       input_name: str | None = None, nodes: int = 1,
                       build_id: str | None = None, job_ids: list[str] | None = None,
                       date: str | None = None, node_type: str | None = None,
                       tag: str | None = None) -> dict:
    """Turn finished benchmark runs into `benchmarks` records and append
    them to this user's records: one per layout (ranks x threads) of one
    input at `nodes` nodes, from a cp2k_bench_parse result (`parsed`,
    local only) or by parsing `stage_dirs` on the login node. Repeats of a
    layout are averaged. A failed, killed, unconverged or disagreeing run
    is recorded with valid: false and a reason. The job IDs go to
    benchmarks_provenance (one per record), never into the benchmark.
    Every record is validated before any is written; an id that already
    exists stops the whole call. Refuses dry-run results.

    Returns {"appended", "valid", "invalid": {id: reason}, "provenance",
    "records", "check_errors"} (check_errors: records_check afterwards).

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        system: the benchmarks `system` block: label, atoms, elements,
            periodic, kpoints (electrons, cell_volume_A3 if known).
        method: the `method` block: functional, basis, cutoff_ry, hybrid,
            scf (dispersion, rel_cutoff_ry if known), as in the input.
        run_type: aimd_step, single_point, geo_opt or cell_opt.
        parsed: a cp2k_bench_parse result; or give stage_dirs instead.
        stage_dirs: stage folders on the cluster, parsed with cp2k_bench_parse.
        input_name: the input to record (e.g. "scan.inp") when the stages
            ran several, e.g. a smoke and a scan input.
        nodes: nodes per run; ranks_per_node is ranks / nodes.
        build_id: the software_registry build timed; omit for the default.
        job_ids: the scheduler job IDs; omit to take them from the runs/<jobid>/
            folder names.
        date: YYYY-MM-DD of the runs; omit for today.
        node_type: hardware label; omit to reuse the facility's records'.
        tag: appended to every id (e.g. the functional) when the same system
            is timed with another method on the same date.
    """
    return cp2k.bench_records(facility, system, method, run_type, parsed, stage_dirs,
                              input_name, nodes, build_id, job_ids, date, node_type, tag)


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
