"""hpc_codes MCP tools: cp2k.

hpc_codes/server.py loads every module in this folder. A new cp2k tool goes here;
a new group of tools is a new module beside this one.

CP2K. These read on the cluster's login node and change nothing there (the
dry run writes only a scratch folder of its own, and removes it), so, unlike
run_command_on_cluster, they need no preview-and-permission step.
"""
from hpc_codes.cp2k import tools as cp2k
from hpc_codes.mcp_tools import _tool


@_tool
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


@_tool
def cp2k_check_input(facility: str, input_path: str, build_id: str | None = None) -> dict:
    """Parse-check a CP2K input on the login node (`cp2k.psmp --check`,
    serial, 60 s limit) with a recorded build, from the input's own folder
    so @INCLUDE files resolve. Proves the syntax, not that the SCF
    converges. Read-only, no permission step needed.

    After a passing check it lists the restart files the input reads at
    run time, as CP2K's own `--echo` resolves them (@SET values, the
    taken @IF branch, @INCLUDEs): the &EXT_RESTART file, and the
    wavefunction when SCF_GUESS is RESTART (WFN_RESTART_FILE_NAME, or
    CP2K's default <PROJECT>-RESTART.wfn), each tested for existence
    relative to the input's folder. A missing wavefunction does not fail
    `ok` (CP2K runs, from the atomic guess) but is named in `warnings`:
    read them before submitting a hybrid. A missing &EXT_RESTART file
    already fails the check.

    Returns {"ok", "build_id", "exit_code", "timed_out", "errors",
    "files": [{"keyword", "path", "exists"}], "warnings", "tail"}.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        input_path: the .inp file on the cluster, absolute or under ~.
        build_id: a software_registry build_id; omit for the default build.
    """
    return cp2k.check_input(facility, input_path, build_id)


@_tool
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


@_tool
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


@_tool
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


@_tool
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


@_tool
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
        run_type: aimd_step, md_step (MLIP or classical MD), single_point,
            geo_opt, cell_opt or train (GPU packs).
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


@_tool
def cp2k_record_from_run(facility: str, parsed: dict, nodes: int, like: str | None = None,
                         system: dict | None = None, method: dict | None = None,
                         job_id: str | None = None, build_id: str | None = None,
                         date: str | None = None, tag: str | None = None,
                         notes: str | None = None, dry_run: bool = False) -> dict:
    """Turn one finished production run (a single point, geo/cell opt or
    MD) into a `benchmarks` record and append it to this user's records,
    with a benchmarks_provenance record for the job ID. Local only: no
    cluster command.

    Before it: fs_download the .out, then hyogo's cp2k_parse_output; pass
    that whole result as `parsed`.

    From the parse: run_type, ranks and threads (ranks_per_node = ranks /
    nodes), atoms and electrons, the date the run started, the timing
    (single point: wall_s = CP2K total time; opt: s_per_step = total time
    / opt steps, first step included, or the steps' own OPT| Used times
    when a killed run has no total; MD: s_per_step), and extra fields:
    forces_s (qs_forces), scf_steps, first_step_s, opt_steps, final_pressure_bar,
    hfx_eri_mib, peak_mib_per_rank, core_hours. From `like` (an existing
    benchmarks id of the same system and method): system, method, build,
    partition and node_type, so nothing is retyped; `system` / `method`
    override single keys (e.g. method={"functional": "PBE0"}). The cell
    volume is never copied from `like`: it comes from the output's cell
    (optimisations) or system={"cell_volume_A3": ...}, since a single point
    may be at another geometry than `like`. A run that
    did not finish without completed steps, or whose SCF did not
    converge, is recorded with valid: false and a reason; an optimisation
    stopped before converging is still a valid per-step timing (warned).
    The id is <facility>-cp2k-<date>-<label>-<runtype>-<tag>-<N>n-<R>x<T>,
    tag defaulting to the functional.

    Returns {"id", "valid", "record", "provenance", "warnings",
    "appended"}. Read the warnings to the user.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        parsed: the cp2k_parse_output result (hyogo), unchanged.
        nodes: nodes the job ran on.
        like: a benchmarks id to copy system, method and build from.
        system: the benchmarks system block, or keys overriding like's.
        method: the benchmarks method block, or keys overriding like's.
        job_id: the scheduler job ID (kept in benchmarks_provenance).
        build_id: the software_registry build; omit to use like's, or the default.
        date: YYYY-MM-DD; omit for the date the run started.
        tag: id part instead of the functional, e.g. "pbe0-hse06cell" when
            the same functional ran twice on one date.
        notes: appended to the generated notes.
        dry_run: build and validate the record but append nothing.
    """
    return cp2k.record_from_run(facility, parsed, nodes, like, system, method, job_id,
                                build_id, date, tag, notes, dry_run)
