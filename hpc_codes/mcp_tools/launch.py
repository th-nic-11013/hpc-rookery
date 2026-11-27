"""hpc_codes MCP tools: launch.

hpc_codes/server.py loads every module in this folder. A new launch tool goes here;
a new group of tools is a new module beside this one.

Launch planning. Local: reads records and pack values, never the cluster.
"""
from typing import Annotated, Literal

from pydantic import Field

from hpc_agent_core import submit_guard
from hpc_codes import launch
from hpc_codes.mcp_tools import _tool


# In the schema, not only the docstring: small models dropped `steps` on "a cell optimisation
# of about 30 steps" (kairo L1-launch-01, 2/10 on 2026-11-26) and got no total estimate.
STEPS = ("MD or optimisation steps the run will take; pass the count the user gives (\"about 30 "
         "steps\" -> 30). Without it the estimate has no total.")


@_tool
def launch_plan(facility: str, code: str, run_type: str, system: dict,
                method: dict | None = None, build_id: str | None = None,
                nodes: int | None = None, optimize: Literal["cost", "time"] = "cost",
                steps: Annotated[int | None, Field(description=STEPS)] = None, hfx_memory_mb: int | None = None,
                gpus: int | None = None, device: Literal["gpu", "cpu"] | None = None,
                layout: str | None = None) -> dict:
    """How to launch a run of `code` on this facility: the build, the
    ranks x threads layout and node count, a job-spec fragment for
    submit_job, and a cost estimate, from the recorded builds and
    benchmark timings. Local only: never contacts the cluster.

    Merge `job_spec` into the submit_job spec and add name, directory,
    executable and attributes.duration (`estimate.suggested_duration`
    when present). Keep `job_spec.plan_signature` when there is one: the
    server's submit guard is on and submit_job refuses a spec without it
    or with other resources or attributes. `basis.match` says how good the timing is: "exact"
    (this system was benchmarked), "similar" (a system within 30 % in
    size; the time is rough) or "none" (a common layout and no estimate;
    benchmark first if the job is long). With no timing of the code on the
    facility at all (a new facility), "none" comes with
    `basis.default_layout`: "pack" (the pack's default) or "generic" (every
    core, 4/2/1 threads per rank, never measured; a warning says so). It is
    signed like any plan, so a guarded harness can submit the short first
    benchmark; set its duration yourself. When the label is recorded but
    not with the `method` asked for, `basis.why` says so and lists the
    recorded values; no time is scaled across methods.

    geo_opt / cell_opt with no optimisation record of this or a similar
    system fall back to a single_point record of the same label and
    method as the per-step time (one energy+forces evaluation): the
    estimate is "s_per_step", rough, times `steps`, and a warning says
    so. It is conservative: on HOKUSAI an HSE06 cell_opt step of 456-atom
    UiO-66 averaged 423 s against 464 s for the single point with
    forces and stress (0.91x). `basis.run_type` names the run type whose
    timing was used. Optimisation records are always preferred.

    Hybrids: when the exact record carries its HFX memory (hfx_eri_mib,
    peak_mib_per_rank, written by cp2k_record_from_run), `memory` gives
    the exchange integrals per rank at the planned layout, the estimated
    peak per rank against the rank's share of node memory, and
    suggested_hfx_memory_mb for cp2k_make_input's hfx.memory_mb. A node
    count whose ranks wouldn't fit is not chosen, and a fixed one gets a
    warning; so does an hfx_memory_mb below the integrals per rank (CP2K
    then computes the rest on the fly and the estimate is too low).
    Rough: it assumes the integrals total about the same at any layout.

    Packs differ (the facility's pack values say which kind):
    - cp2k fills every core of every node (R x T = cores per node).
    - a partial-node pack (lammps: MLIP MD) may use fewer cores than the
      node has; the cost counts the cores used, optimize="cost" picks the
      fewest core-seconds per step, and a warning says the node is shared.
      With nothing recorded it returns the pack's default layout.
    - a GPU pack (mace: training, replay selection) takes `gpus`: one
      process per GPU on one node, each with the cores a GPU is billed
      as. No timing is recorded for GPU runs, so `estimate` is null and
      the walltime is yours to set. `layout.distributed` is true for more
      than one GPU: pass it to mace_make_finetune's `distributed`.
      device="cpu" plans the same runs on CPU nodes from the facility's
      measured layout (<CODE>_CPU): one process, threads bound to cores
      (OMP_PROC_BIND/OMP_PLACES and srun --cpu-bind), the node to itself
      where the facility says so. Unbound or spread over both sockets,
      MACE training ran 4-10x slower. Refused where nothing is measured.

    Layout override: layout="RxT" with nodes=N takes that layout instead
    of the best one, only when this system (label), method and run type
    has a valid record at R x T on N nodes ("exact" match); the plan is
    signed for it. Anything else is refused, naming the recorded valid
    layouts per node count: pick one of them, or benchmark first.

    run_type="benchmark" (a CPU pack with benchmark scripts, e.g. cp2k)
    plans the layout-scan benchmark job itself, as bench_job.sh asks: one
    exclusive node, every core one process of one thread, no launcher
    (the scripts launch each run), `attributes.duration` set from the
    stage timeouts and the pack's smoke and scan layouts (at least
    00:30:00, at most 00:53:00; also `estimate.suggested_duration`, with
    the worst case in `estimate.worst_case_core_hours`), and
    `benchmark.environment` (BENCH_LIMIT_S, SMOKE_LAYOUTS, SCAN_LAYOUTS,
    SMOKE_TIMEOUT, SCAN_TIMEOUT), already in `job_spec.environment`. Submit
    it as `job_spec` plus name, directory (the uploaded benchmark folder)
    and cp2k_bench_input(scripts=true)'s `submit_fields`: its executable,
    and its environment ADDED to job_spec.environment (keep
    BENCH_LIMIT_S: the scripts' time guard reads it). nodes other than 1
    is refused.

    Returns {"build", "layout", "basis": {match, run_type, why, record_ids,
    options: [per node count: layout, value, core_hours_per_unit,
    efficiency, memory (hybrids)]}, "job_spec", "estimate" | null,
    "memory": {eri_total_mib, eri_per_rank_mib, est_peak_mib_per_rank,
    limit_mib_per_rank, fits, suggested_hfx_memory_mb, record_id, rough}
    | null, "warnings"}, plus "benchmark": {environment, smoke_layouts,
    scan_layouts, runs, needed_s, limit_s} for run_type="benchmark". Read
    the memory warnings to the user.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        code: code pack, e.g. "cp2k".
        run_type: aimd_step, md_step (MLIP or classical MD), single_point,
            geo_opt, cell_opt, train (GPU packs) or benchmark (the
            layout-scan benchmark job).
        system: mapping with key "atoms" (int, required); optional "label"
            (str, e.g. "UiO-66-conv") finds an exact record, "electrons"
            (int) sharpens "similar". e.g. {"atoms": 456, "label": "UiO-66-conv"}.
        method: optional mapping of exact filters on the recorded method;
            keys are the benchmarks method fields: functional (str), hybrid
            (bool), basis (str), cutoff_ry (float), dispersion (str: records
            say "D3", "D3BJ" or null; cp2k_make_input's d3 "zero", "bj",
            "none" match them too), model (str, MLIP), e.g. {"functional": "revPBE", "hybrid": false,
            "cutoff_ry": 450}.
        build_id: a software_registry build_id; omit for the default build.
        nodes: fix the node count; omit to choose one.
        optimize: "cost" (fewest core-hours, the default) or "time"
            (fastest at >= 70 % parallel efficiency).
        steps: MD or optimisation steps the run will take; pass the count the
            user gives ("about 30 steps" -> 30). Without it no total estimate.
        hfx_memory_mb: the HFX MAX_MEMORY per MPI rank the input will use
            (cp2k_make_input's hfx.memory_mb, 3000 by default there), to
            check the integrals fit in it.
        gpus: GPUs for a GPU pack (default 1); refused for a CPU pack.
        device: "cpu" to run a GPU pack's job (mace: training, replay
            selection) on CPU nodes; omit (or "gpu") for GPUs.
        layout: "<ranks per node>x<threads per rank>", e.g. "16x7", with
            nodes: use that recorded layout (see above); omit to choose.
    """
    result = launch.plan(facility, code, run_type, system, method, build_id, nodes,
                         optimize, steps, hfx_memory_mb, gpus, device, layout)
    if submit_guard.enabled():
        # G95: submit_job then accepts only this job_spec's resources and attributes.
        result["job_spec"]["plan_signature"] = submit_guard.sign(facility, result["job_spec"])
    return result
