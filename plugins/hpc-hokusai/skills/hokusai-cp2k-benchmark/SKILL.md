---
name: hokusai-cp2k-benchmark
description: Benchmark CP2K on HOKUSAI BigWaterfall2 (HBW2) - find the fastest MPI-rank x OpenMP-thread layout and node count for a system and run type (AIMD, single point, geometry optimisation), first from stored benchmark records and otherwise with a fail-fast, cost-capped single Slurm job that is dry-run safely on the login node; record the timings. Use whenever the user wants to benchmark, time, profile, scale-test or tune CP2K on HOKUSAI BigWaterfall2 (HBW2), asks how many nodes, ranks or threads to use, asks why a CP2K run is slow, or is about to start a long CP2K run.
---

# Benchmarking CP2K on HOKUSAI BigWaterfall2 (HBW2)

Every tool call uses `facility="hokusai"`. A benchmark spends real core
time. Show the user the whole plan, the expected and worst-case
core-hours, and the job spec, then wait for permission.

This file is the method; it holds for CP2K on any Slurm machine.
**HOKUSAI BigWaterfall2 (HBW2) values** (partition, node shape, scan layouts, binding
line, front-end Python, job spec, cost) are in `references/facility.md`.
Measured timings are `benchmarks` records, read and written through
`hokusai-records`. `references/lessons.md` explains why the workflow
looks the way it does.

## Contract

- **Reads:**
  - `benchmarks`: earlier timings;
  - `software_registry`: which build to time (`default: true` unless the
    user names one).
- **Writes:** `benchmarks`, one record per layout × node count, with the
  job ID.
- **Tools:**
  - `fs_upload`, `run_command_on_cluster` and `fs_view` / `fs_tail` on the
    login node;
  - `submit_job` and `get_job_status` for the job;
  - `hokusai-records` locally.
- **Preconditions:**
  - a build (`hokusai-cp2k-build`);
  - the production input, written by its run skill;
  - for a new or bigger cell, a converged single point;
  - the user's approval of the cost.
- **Done when:**
  - the records are appended (including failed runs, as `valid: false`);
  - a layout and node count, with the measured s/step, are handed back to
    the run skill;
  - the production cost is projected.

## 1. Look for an existing measurement

Query before spending anything:

```bash
python3 -I <hokusai-records>/scripts/records.py query benchmarks code=cp2k \
  run_type=<aimd_step|single_point|geo_opt> system.label=<label> valid=true \
  --fields=nodes,ranks_per_node,threads_per_rank,metric.value,method.scf,date
```

If nothing matches the label, query by `code`, `run_type` and
`partition`. Then compare `system.atoms`, `system.elements`,
`system.kpoints` and `method.*` yourself. A record answers the question when
**all** of these hold:
- the method is the same: functional, hybrid or not, basis family,
  cutoff within ~10%, and the same kind of SCF (OT or diagonalisation);
- the system is similar: atom count within ~2×, a similar composition
  class (water, MOF, oxide...), and k-points;
- the build is the same CP2K version, or one close to it.

Then use the fastest valid layout at the node count wanted. Treat layouts
within 3% of it as ties.

**Benchmark again only when:**
- nothing matches;
- the system differs a lot: size, a hybrid functional, diagonalisation
  instead of OT, k-points;
- the CP2K build is new;
- a long production run makes a few percent worth the cost.

Say which records you relied on, by `id`.

## 2. Pick the input from its run skill

Benchmark the input that will actually run, shortened:

| production run | build the input with | shorten it to | metric (`parse_bench.py`) |
|---|---|---|---|
| AIMD | `hokusai-cp2k-aimd` | `STEPS 10`; NVE is fine | s per MD step, mean over steps ≥ 3 |
| single point | `hokusai-cp2k-single-point` | unchanged | s per single point (detected automatically) |
| geometry optimisation | `hokusai-cp2k-geo-opt` | `MAX_ITER 3` | whole CP2K time (treated like a single point). It ranks layouts, but is not a per-step time |

Rules:
- **Keep every setting identical to production.** A different cutoff,
  `EPS_SCF` or functional makes the result meaningless. Bundled
  low-cutoff test inputs overstate how much the layout matters.
- **Inline or absolute includes.** `run_layout` copies only the input file
  into each run folder. Change `@INCLUDE subsys.inc` to an absolute path, or
  build the input with `scripts/make_cp2k_md_input.py`, which writes the
  coordinates inline.
- **Test the SCF of any new or bigger cell with a single point first.** An
  unconverged SCF once bought no timing for a 4-node job (lessons 9–10).
  AIMD benchmark inputs set `IGNORE_CONVERGENCE_FAILURE`, and the gate
  fails on `SCF nc` > 0.
- **Parse-check every input** on the login node, in a scratch copy, using
  the command in `references/facility.md`.

Helpers (run them with `/usr/bin/python3.12 -I` on the login node):
- `scripts/make_cp2k_md_input.py STRUCT.xyz --name NAME --out-dir <bench>/inputs`
  writes a 10-step NVE input and a 3-step smoke version from an extended
  XYZ.
  - Its defaults are revPBE-D3, 450/60 Ry and `FULL_SINGLE_INVERSE`.
  - Pass `--data-dir` for a build other than its default.
  - Use `FULL_ALL` for cells of several hundred atoms.
- `scripts/make_sp_inputs.py` turns MD inputs into FULL_ALL single points.
  Edit its `SOURCES` list for the system.

## 3. The benchmark job

One job on one exclusive node, with three fail-fast stages:

| stage | input | purpose |
|---|---|---|
| 1 smoke | `SMOKE_INPUT` (~3 steps) in 2 layouts | it runs at all, the binding is right, the energies agree |
| 2 scan | `SCAN_INPUT` (≥ 10 steps) in `112x1 56x2 28x4 16x7 8x14 4x28` | the timing |
| 3 validate | `VALIDATE_INPUT` (bigger, or `none`) with the 3 best layouts + a repeat of the best | the ranking holds; the noise level |

The layouts and binding are in `references/facility.md`.

Each stage stops at its first failed run. The job never starts a run that
could outlast its time limit: each run has a `timeout`, and a guard checks
the time left. Schedulers that allow overtime past `--time` would
otherwise let the job run long.

Set it up on the login node:

```bash
mkdir -p ~/agent/work/cp2k-runs/<system>/bench/inputs
```

`fs_upload` every file from `scripts/` into `bench/`, and the inputs into
`bench/inputs/`. Then edit **only the BENCHMARK SETTINGS block** of
`bench/bench_job.sh`:
- the inputs and the per-run timeouts;
- optionally `SMOKE_LAYOUTS`, `SCAN_LAYOUTS` and `VALIDATE_PICK`;
- `CP2K_ENV`: the build's `env_script`;
- `BENCH_LIMIT_S`, which must equal the job's duration in seconds.

Timeouts: about 2× the fastest layout's run time, plus at least 2 min of
launch slack (lesson 11). With no timing yet, run the smoke stage alone
first, or scan only 3–4 layouts.

**Dry-run it before submitting.** It runs on the login node, in a scratch
copy, with a stub `srun`. The harness refuses to run unless that stub is
first on `PATH`.

```bash
REF_OUT=<finished cp2k.out> SMOKE_INPUT=... SCAN_INPUT=... VALIDATE_INPUT=... \
  bash ~/agent/work/cp2k-runs/<system>/bench/dryrun.sh ~/agent/work/cp2k-runs/<system>/bench <empty scratch dir>
```

Repeat it twice more:
- with `FAIL_LAYOUT=<a scan layout>`: the stage must stop;
- with a small `BENCH_LIMIT_S`, such as 400: the guard must refuse a run.

This tests the stage logic, not CP2K. The stub copies a finished `cp2k.out`
from `REF_OUT`, which is required: use the H2O-32 test output of the build
being benchmarked (the build skill runs that test). Never run the stage
scripts outside `dryrun.sh` on a login node, because a real `srun` there
tries to start a job.

Submit with the spec in `references/facility.md`. Under `submit_job`, the
spec sets the resources and the job runs in `directory`; the script's own
`#SBATCH` lines are only for a manual `sbatch`. Then:
- Tell the user the worst-case core-hours, and that an exclusive node can
  take hours to come free.
- Use no `launcher`: the stage scripts start one `srun` step per layout
  themselves.
- Don't edit the stage scripts or inputs while the job is pending: the job
  reads them when it starts, not at submission.

## 4. Read the results

The job log (`slurm-<jobid>.out`) ends with the summary. To re-read:

```bash
/usr/bin/python3.12 -I bench/parse_bench.py bench/runs/<jobid>/stage2
```

It reports, per layout:
- s per step (or per single point) and the speed-up against pure MPI;
- core-hours per step and peak memory per rank;
- the main timers: grid, `dbcsr_multiply_generic`, FFT;
- `dE_max` between layouts, and `dF_max` for single points;
- `SCF nc`.

**Look at `SCF nc` before any timing.** Any unconverged SCF after the
first force evaluation makes the step time meaningless.

Interpret, don't just rank:
- **A win under 3% is a tie.** Stage 3's repeat shows the noise.
- **Where the time goes explains the ranking.** Grid-bound runs favour
  more ranks; DBCSR- or diagonalisation-bound runs favour more threads.
- **Energies across layouts agree to ≤ 1e-6 Ha.** A larger difference is a
  broken run, not noise.
- **Memory:** memory per rank × ranks per NUMA domain must stay well under
  the memory of one domain.
- **Single points are not MD steps.** The AIMD winner is not automatically
  the single-point winner (lesson 13). Record and choose them separately.

### Multi-node follow-up

Only after a single-node scan has passed. `scripts/multinode_layouts_job.sh`:
- runs one input on N nodes in several per-node layouts, baseline first;
- reads the 1-node reference run (`REF_DIR`, `REF_LAYOUT`) to set every
  run's timeout;
- prints the speed-up and parallel efficiency against that reference.

Submit it like the job above, with `node_count` N. `LAYOUTS` and
`REF_LAYOUT` have no default: set them. Dry-run it first with
`JOB=multinode_layouts_job.sh DRY_NODES=N`. Choose the layouts with the
user, each with a reason:
- the 1-node winner (the baseline);
- one with fewer ranks per node (less network traffic);
- one with more ranks per node (wider grid and FFT distribution).

Query `benchmarks` for the same system at other node counts first. They
may already show where the efficiency drops.

## 5. Record

Append one `benchmarks` record per layout × node count through
`hokusai-records` (the schema is its `schema.yaml`):
- **Identity:** `facility`, `partition`, `node_type` (from
  `references/facility.md`), and the build's `build_id`.
- **System:**
  - `label`, `atoms` and `elements` (from the structure);
  - `cell_volume_A3`, `periodic` and `kpoints`.
- **Method:** functional, dispersion, basis, cutoffs, `hybrid`, and the SCF
  as used.
- **Run:**
  - `run_type`, `nodes`, `ranks_per_node`, `threads_per_rank`;
  - `metric` (`s_per_step` or `wall_s`);
  - `extra`: `first_step_s`, `parallel_efficiency`, `core_hours`,
    `scf_steps`, where known.
- **Provenance:** `job_id`, `date`, `valid`.
  - A failed, timed-out or unconverged run gets `valid: false` and a
    `reason`.

Run `records.py check` afterwards. Keep `bench/results.md` in the benchmark
folder for the full tables, if the user wants it. Records hold the numbers
other skills use.

Hand the chosen layout and node count back to the production run skill,
and project the production cost: s/step × steps × nodes × cores per node ÷
3600 core-hours. Offer to share the new records (`hokusai-records`,
"Sharing a benchmark"), but don't do it unasked.
