---
name: {{SLUG}}-vasp-benchmark
description: Benchmark VASP on {{DISPLAY_NAME}} - find the fastest MPI ranks x KPAR x NCORE layout for a system, first from stored benchmark records and otherwise with one fail-fast, cost-capped Slurm job that times a fixed number of SCF steps per layout with NBANDS pinned; dry-run it safely, then record the timings. Use whenever the user wants to benchmark, time, scale-test or tune VASP on {{DISPLAY_NAME}}, asks which NCORE, KPAR or NPAR or how many ranks or nodes to use, asks why VASP runs are slow, or is about to start many VASP single points (e.g. labelling a training set).
---

# Benchmarking VASP on {{DISPLAY_NAME}}

Every `hpc` tool call uses `facility="{{SLUG}}"`. A benchmark spends real
core time. Show the user the whole plan, the expected and worst-case
core-hours, and the job spec, then wait for permission.

This file is the method; it holds for VASP on any Slurm machine. Measured
timings are `benchmarks` records, read and written through
`{{SLUG}}-records`. `references/lessons.md` explains why the workflow looks
the way it does: read it once.

## Contract

- **Reads:** `benchmarks` (earlier timings) and `software_registry` (the
  build: `default: true` unless the user names one).
- **Writes:** `benchmarks`, one record per layout (`records_append`), and
  `benchmarks_provenance` with the job ID.
- **Tools:** `launch_plan`, `records_query`, `records_append`,
  `records_check` (local); `vasp_make_incar`, `vasp_parse_output` (hyogo,
  local); `fs_mkdir`, `fs_upload`, `fs_cp`, `submit_job`, `get_job_status`,
  `fs_tail`, `fs_download` (cluster).
- **Preconditions:**
  - a VASP build in `software_registry` (`{{SLUG}}-records`);
  - the production input (`{{SLUG}}-vasp-single-point`), including a
    converged single point of this system, so NBANDS and the SCF are known;
  - the user's approval of the cost.
- **Done when:**
  - a record is appended for every layout run (failed ones as
    `valid: false`);
  - the fastest layout (ranks, KPAR, NCORE) and its s per SCF step are handed
    back to the run skill, with the production cost projected.

## 1. Look for an existing measurement

```text
launch_plan(facility="{{SLUG}}", code="vasp", run_type="single_point",
            system={"label": "<label>", "atoms": <atoms>, "electrons": <NELECT if known>},
            method={"functional": "<functional>"})
```

- `basis.match` `"exact"`: this system and method were timed; the chosen
  record (`basis.record_ids`) holds KPAR and NCORE in `layout`. No benchmark
  needed.
- `"similar"`: a system within 30 % in size. Check the record before
  relying on it: same functional and POTCAR set, ENCUT within ~10 %, ISPIN,
  a similar k-point count (`system.kpoints`), the same VASP version and
  build flavour.
- `"none"`: benchmark, unless the whole campaign costs less than the
  benchmark.

For what `launch_plan` doesn't return, query directly:
`records_query(facility="{{SLUG}}", record_type="benchmarks", filters={"code": "vasp", "system.label": "<label>", "valid": true}, fields=["ranks_per_node", "layout", "metric", "extra", "nodes"])`.

**Benchmark again only when:** nothing matches; the k-point count, ISPIN,
spin-orbit or the atom count changed a lot; the build is new; or a long
campaign makes a few percent worth it. Say which records you relied on, by
`id`.

## 2. The benchmark input

One single point of the production system, from its run skill, changed in
exactly four ways with `vasp_make_incar` (local):

```text
vasp_make_incar(poscar_path="<local>/POSCAR", base_incar="<local>/INCAR.production",
                nbands=<the converged run's NBANDS>,
                settings={"NELM": 10, "NELMIN": 10, "NSW": 0, "LWAVE": false, "LCHARG": false},
                out_path="<local>/bench/inputs/INCAR")
```

- **NBANDS pinned** (`nbands`, or `nbands_from=<the converged run's
  OUTCAR>`): VASP rounds NBANDS up to a multiple of the band groups,
  ranks / (KPAR x NCORE), so without a pin each layout would solve a
  different problem. The job refuses an INCAR without NBANDS, and any layout
  whose band groups don't divide it.
- **NELMIN = NELM** (about 10): every layout runs exactly that many SCF
  steps, so the per-step times compare directly. These runs end at NELM on
  purpose and are not results.
- **NSW 0**, and `LWAVE`/`LCHARG` off: one ionic step, nothing large
  written.
- Everything else, the physics included (ENCUT, PREC, ISPIN, MAGMOM, LORBIT,
  the k-mesh), stays as in production. A cheaper test input ranks layouts
  for a different problem.
- Put KPAR, NCORE and NPAR, if present, on lines of their own: the job
  replaces those lines for each layout.

## 3. Choose the layouts

A layout is `RANKS:KPAR:NCORE` on one node; the first is the baseline (the
current production layout, or `{{VASP_DEFAULT_LAYOUT}}` with NCORE 4). The
facility default list is `{{VASP_SCAN_LAYOUTS}}`. Rules:

- KPAR divides RANKS, and NCORE divides RANKS / KPAR; the band groups
  RANKS / (KPAR x NCORE) must divide the pinned NBANDS. The job checks all of
  this before it runs anything.
- KPAR <= the irreducible k-points (`params.NKPTS` of the converged run).
  KPAR > 1 multiplies memory per rank and was slower in the campaign this
  method comes from: include one or two, don't assume.
- Scan NCORE first: on 48 ranks NCORE 12 beat NCORE 4 by 1.33x there.
  Divisors of the ranks per socket or NUMA domain are the usual candidates.
- RANKS = {{VASP_CORES_PER_NODE}} (the full node) for records `launch_plan`
  can use. A layout with idle cores is still timed and recorded, but
  `launch_plan` plans only full-node layouts for this pack.
- 5-8 layouts; the job repeats the baseline at the end (the noise level).

## 4. The job

Set up the folder on {{DISPLAY_NAME}}:

```text
~/agent/work/vasp-runs/<system>/bench/   bench_job.sh
~/agent/work/vasp-runs/<system>/bench/inputs/   INCAR  POSCAR  KPOINTS  POTCAR
```

`fs_mkdir` both, `fs_upload` `scripts/bench_job.sh` and the inputs, and
`fs_cp` the run's POTCAR into `inputs/`. The job's settings are
environment variables (defaults in the script's BENCHMARK SETTINGS block):

| variable | value |
|---|---|
| `VASP_ENV` | the build's `env_script` (required) |
| `VASP_EXE` | `vasp_<variant.exe>`: `vasp_std`, or `vasp_gam` for a Gamma-only mesh |
| `LAYOUTS` | the layouts from step 3 |
| `RUN_TIMEOUT` | s per run: about 2x the slowest expected run (s per SCF step x NELM x 2) plus 2 min |
| `BENCH_LIMIT_S` | the job's duration in seconds, exactly |

What the job does: checks the input and every layout; then per layout
copies the inputs into `runs/<jobid>/<NN>-r<R>-k<K>c<C>/`, writes KPAR and
NCORE, and runs `srun {{VASP_SRUN_MPI}} --ntasks=R --cpus-per-task=1
--cpu-bind=cores` under `timeout`. It stops at the first run that fails,
times out, leaves an unfinished OUTCAR or runs with another NBANDS, and never
starts a run that could outlast the job (timeout + 90 s against the time
left). The log ends with a table: steps, LOOP+ time, s per SCF step, speed-up
and energy difference against the baseline.

**Dry-run it first, on this computer** (any bash with GNU sed and awk), in a
scratch copy of the bench folder with the real inputs:

```bash
DRYRUN=1 REF_OUTCAR=<the converged run's OUTCAR> bash bench_job.sh                  # passes
DRYRUN=1 DRYRUN_FAIL=2 REF_OUTCAR=<...> bash bench_job.sh                           # stops at run 2
DRYRUN=1 BENCH_LIMIT_S=60 REF_OUTCAR=<...> bash bench_job.sh                        # refuses the first run
```

A dry run never calls `srun` or VASP: each "run" copies `REF_OUTCAR`. It
tests the input checks, the layout rules, the stops and the table, not VASP.
Without a local shell, show the user these commands and run them on the
login node only with their permission (`{{SLUG}}-remote-command`).

Submit from the bench folder (absolute `directory`: `sbatch --chdir` does
not expand `~`):

```json
{
  "name": "vasp-bench-<system>",
  "directory": "/home/<user>/agent/work/vasp-runs/<system>/bench",
  "executable": "bash bench_job.sh",
  "environment": {"VASP_ENV": "<env_script>", "VASP_EXE": "vasp_std",
                  "LAYOUTS": "<layouts>", "RUN_TIMEOUT": "<s>", "BENCH_LIMIT_S": "<s>"},
  "resources": {"node_count": 1, "processes_per_node": {{VASP_CORES_PER_NODE}},
                "cpu_cores_per_process": 1, "exclusive_node_use": true},
  "attributes": {"queue_name": "{{VASP_PARTITION}}", "duration": "<HH:MM:SS>"}
}
```

- No `launcher`: the script starts one `srun` step per layout.
- Worst-case cost: {{VASP_CORES_PER_NODE}} x duration in hours, in
  core-hours. Expected: the sum of the runs' times.
- Don't edit the script or the inputs while the job is pending: it reads
  them when it starts.

### On {{DISPLAY_NAME}}

{{CODE_NOTES}}

If no notes appear above, the facility has none for VASP yet: check
`search_docs(facility="{{SLUG}}", query="VASP")` for module lines, MPI
launch flags and node shape before the first job.

## 5. Read the results

`fs_tail` the job log for the table. Then `fs_download` every run's
`OUTCAR` (and `OSZICAR`) and read each with
`vasp_parse_output(outcar_path="<local>/runs/<NN>-.../OUTCAR")`:

- `layout` (`ranks`, `kpar`, `ncore`, `band_groups`) must be the planned
  one and `params.NBANDS` the pinned value; otherwise the run timed another
  problem.
- s per SCF step = `cost.loop_s[0]` / `scf.steps[0]`; wall for the run =
  `cost.loop_s[0]` (the one ionic step).
- `converged` false with a NELM warning is expected here, by design
  (NELMIN = NELM): the run stopped at its fixed step count. A `messages` error box, `nul_bytes` > 0 or
  `forces.complete` false is not expected: that run is not a timing.
- Energies (`energy.toten`) after the same fixed steps should agree across
  layouts to well below 1 meV per cell; a larger difference means the runs
  differ in more than the layout.

Interpret, don't just rank:
- **A win under 3 % is a tie.** The baseline's repeat shows the noise.
- **The SCF step count decides the production cost**, not the layout: a
  frame needing 150 steps costs 5x one needing 30 at any layout. Report the
  layout gain next to the step counts the production runs need.
- **Memory:** KPAR > 1 keeps a copy of the wavefunctions per k-point group;
  check `cost.max_memory_mb` against the node.

## 6. Record

One `benchmarks` record per layout run (the baseline's repeat averaged into
its first run), with `records_append`. The fields are in
`{{SLUG}}-records`; for VASP:

```text
records_append(facility="{{SLUG}}", record_type="benchmarks", record={
  "id": "{{SLUG}}-vasp-<date>-<label>-singlepoint-1n-<R>x1-k<KPAR>c<NCORE>",
  "facility": "{{SLUG}}", "partition": "{{VASP_PARTITION}}", "node_type": "<hardware label>",
  "code": "vasp", "version": "<build version>", "build_id": "<build_id>",
  "system": {"label": ..., "atoms": ..., "elements": {...}, "periodic": "xyz",
             "kpoints": "<mesh, e.g. 3x3x1>", "electrons": <NELECT>},
  "method": {"functional": "PBE", "basis": "<POTCAR set, e.g. PAW_PBE 64>",
             "cutoff_ry": <ENCUT / 13.6057>, "hybrid": false,
             "scf": "<ALGO, EDIFF, ISPIN as run>"},
  "run_type": "single_point", "nodes": 1, "ranks_per_node": <R>, "threads_per_rank": 1,
  "layout": {"KPAR": <KPAR>, "NCORE": <NCORE>},
  "metric": {"name": "wall_s", "value": <cost.loop_s[0]>},
  "extra": {"scf_steps": <scf.steps[0]>},
  "date": "<date>", "valid": true,
  "notes": "fixed-step timing: NELMIN = NELM, NBANDS <n> pinned"})
```

- `metric.value` is the wall time of **this many SCF steps**
  (`extra.scf_steps`), not of a converged single point: the run skill scales
  it by the steps a production frame needs.
- A run that failed, timed out or ran with another NBANDS gets
  `"valid": false` and a `reason`, so it is not repeated.
- Then the provenance:
  `records_append(facility="{{SLUG}}", record_type="benchmarks_provenance", record={"id": "<record id>", "benchmark_id": "<record id>", "job_ids": ["<jobid>"]})`,
  and `records_check(facility="{{SLUG}}")`: its errors must be empty.

Hand the fastest layout back to `{{SLUG}}-vasp-single-point` (ranks, KPAR,
NCORE for `vasp_make_incar`'s `parallel`), and project the production cost:
s per SCF step x SCF steps per frame x frames x {{VASP_CORES_PER_NODE}} /
3600 core-hours. Before switching production to the new layout, run one full
converged frame with it and compare it with its earlier label
(`labels_compare`): with NBANDS pinned they agree. Delete the benchmark's run
folders once recorded. Offer to share the records (`{{SLUG}}-records`,
"Sharing a benchmark"), but don't do it unasked.
