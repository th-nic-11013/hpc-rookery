"""hpc_codes MCP tools: launch.

hpc_codes/server.py loads every module in this folder. A new launch tool goes here;
a new group of tools is a new module beside this one.

Launch planning. Local: reads records and pack values, never the cluster.
"""
from hpc_codes import launch
from hpc_codes.mcp_tools import _tool


@_tool
def launch_plan(facility: str, code: str, run_type: str, system: dict,
                method: dict | None = None, build_id: str | None = None,
                nodes: int | None = None, optimize: str = "cost",
                steps: int | None = None, hfx_memory_mb: int | None = None,
                gpus: int | None = None) -> dict:
    """How to launch a run of `code` on this facility: the build, the
    ranks x threads layout and node count, a job-spec fragment for
    submit_job, and a cost estimate, from the recorded builds and
    benchmark timings. Local only: never contacts the cluster.

    Merge `job_spec` into the submit_job spec and add name, directory,
    executable and attributes.duration (`estimate.suggested_duration`
    when present). `basis.match` says how good the timing is: "exact"
    (this system was benchmarked), "similar" (a system within 30 % in
    size; the time is rough) or "none" (a common layout and no estimate;
    benchmark first if the job is long). When the label is recorded but
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

    Returns {"build", "layout", "basis": {match, run_type, why, record_ids,
    options: [per node count: layout, value, core_hours_per_unit,
    efficiency, memory (hybrids)]}, "job_spec", "estimate" | null,
    "memory": {eri_total_mib, eri_per_rank_mib, est_peak_mib_per_rank,
    limit_mib_per_rank, fits, suggested_hfx_memory_mb, record_id, rough}
    | null, "warnings"}. Read the memory warnings to the user.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        code: code pack, e.g. "cp2k".
        run_type: aimd_step, md_step (MLIP or classical MD), single_point,
            geo_opt, cell_opt or train (GPU packs).
        system: {"atoms": int} at least; "label" (e.g. "UiO-66-conv")
            finds an exact record, "electrons" sharpens "similar".
        method: optional exact filters on the recorded method, e.g.
            {"functional": "revPBE", "hybrid": false, "cutoff_ry": 450}.
        build_id: a software_registry build_id; omit for the default build.
        nodes: fix the node count; omit to choose one.
        optimize: "cost" (fewest core-hours, the default) or "time"
            (fastest at >= 70 % parallel efficiency).
        steps: MD or optimisation steps, for the total estimate.
        hfx_memory_mb: the HFX MAX_MEMORY per MPI rank the input will use
            (cp2k_make_input's hfx.memory_mb, 3000 by default there), to
            check the integrals fit in it.
        gpus: GPUs for a GPU pack (default 1); refused for a CPU pack.
    """
    return launch.plan(facility, code, run_type, system, method, build_id, nodes,
                       optimize, steps, hfx_memory_mb, gpus)
