# `launch_plan`: the hyogo → rookery seam

Built 2026-11-04: `hpc_codes/launch.py` (e69cb9e), skills rewired (b943ba5).

## Job

hyogo knows *what* to run (an input, a method, a system). rookery knows
*how* to run it on a machine. `launch_plan` turns the first into the
second, from records that already exist (`software_registry`,
`benchmarks`, 42 HOKUSAI timings) and the pack values. hyogo never names
a partition, a layout, an `srun` flag or an env script.

Read-only, local: it reads records and values and never contacts the
cluster (hyogo calls `cp2k_check_build` separately if it wants proof).

## Call

```text
launch_plan(
  facility, code,                  # "hokusai", "cp2k"
  run_type,                        # aimd_step | single_point | geo_opt | cell_opt
  system,                          # {label?, atoms, elements?, electrons?}
  method=None,                     # {functional?, hybrid?, cutoff_ry?}; hybrid matters most
  build_id=None,                   # default build when omitted
  nodes=None,                      # fix the node count, else chosen (below)
  optimize="cost",                 # "cost" or "time"
  steps=None,                      # MD/opt steps, for the estimate
)
```

## Result

```text
{
  "build":   {build_id, version, env_script, data_dir},
  "layout":  {nodes, ranks_per_node, threads_per_rank},
  "basis":   {match: exact | similar | none, record_ids: [...], why: "..."},
  "job_spec": {                     # merge into submit_job; hyogo adds name,
    "pre_launch": "source <env>",   #   directory, executable, duration
    "launcher": "srun --mpi=pmix --cpus-per-task=7 --cpu-bind=cores --distribution=block:block",
    "environment": {"OMP_NUM_THREADS": "7", "OMP_PLACES": "cores", "OMP_PROC_BIND": "close"},
    "resources": {"node_count": 2, "processes_per_node": 16, "cpu_cores_per_process": 7},
    "attributes": {"queue_name": "mpc"}
  },
  "estimate": {metric: s_per_step | wall_s, value, core_hours_per_unit,
               total_wall_s, total_core_hours, suggested_duration} | null,
  "warnings": [...]                 # e.g. "timing from the gcc build, you asked for intel23"
}
```

## How it picks

1. **Build:** `build_id`, else the registry default; removed builds are
   skipped (same `resolve_build` the CP2K tools use).
2. **Records:** valid `benchmarks` for this code, run_type and facility.
3. **Match ladder:**
   - *exact*: same `system.label` (and `method.hybrid`);
   - *similar*: same run_type and hybrid flag, atoms (or electrons, when
     both given) within ±30 %; the closest wins;
   - *none*: no record → fall back (below).
4. **Nodes** (when not fixed), among the matched records:
   - `cost`: the fewest core-hours per step, i.e. the node count with the
     best parallel efficiency; ties → fewer nodes;
   - `time`: the fastest whose parallel efficiency is ≥ 0.7, so it does
     not burn 2× the core time for 10 % speed.
5. **Layout:** the fastest valid R×T at that node count.
6. **Estimate:** exact → the record's value. Similar → the neighbour's
   value **unscaled**, flagged "rough" (scaling by atoms³ looks precise
   and isn't). None → `null`.
7. **Fallback (match none):** the layout that wins most often across all
   records of this run_type on the partition, a warning, and
   "run `<slug>-cp2k-benchmark` first if the job is long".

## Staying code- and scheduler-generic

The launcher line is not hard-coded. It becomes a pack value with a
facility override, so a PBS or PJM facility, or another code, fills its
own:

```json
"CP2K_LAUNCH": {
  "pre_launch": "source {env_script}",
  "launcher": "srun {CP2K_SRUN_MPI} --cpus-per-task={T} --cpu-bind=cores --distribution=block:block",
  "environment": {"OMP_NUM_THREADS": "{T}", "OMP_PLACES": "cores", "OMP_PROC_BIND": "close"}
}
```

Matching, node choice and estimates are generic over the `benchmarks`
schema, so a second code only needs its own `*_LAUNCH` value.

## What changes elsewhere

- The three run skills (single-point, geo-opt, aimd) replace their
  "query benchmarks, else …" paragraph and the hand-filled job-spec
  placeholders with one `launch_plan` call. Their contract lists it under
  Tools.
- `benchmark` skill: its "should I benchmark first?" judgment reads
  `basis.match` (none or similar on a long job → benchmark).
- These run skills move to hyogo later; the call stays the same.

## Tests (offline)

The shipped HOKUSAI records are the fixture: H2O-64 exact hit, a 200-atom
"similar" hit, an unknown hybrid system (none → fallback), `time` vs
`cost` picking different node counts on UiO-66-conv, a removed build
refused, an unconfigured facility still answered (it is local).

## Open

- Should an unconfigured facility be answered? Lean **yes**: nothing
  reaches the cluster, like `get_facility` (packaged data).
- Default `optimize`: lean **cost** (core time is the scarce thing on
  allocated projects).
