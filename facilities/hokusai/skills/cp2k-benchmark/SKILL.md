---
name: {{SLUG}}-cp2k-benchmark
description: Benchmark CP2K on {{DISPLAY_NAME}} - find the fastest MPI-rank x OpenMP-thread layout and node count for a system and run type (AIMD, single point, geometry optimisation) with a fail-fast, cost-capped single Slurm job, dry-run it safely on the login node, and read the timings. Use whenever the user wants to benchmark, time, profile, scale-test or tune CP2K on {{DISPLAY_NAME}}, asks how many nodes, ranks or threads to use, asks why a CP2K run is slow, or is about to start a long CP2K run.
---

# Benchmarking CP2K on {{DISPLAY_NAME}}

Every tool call uses `facility="{{SLUG}}"`. A benchmark spends real core
time. Show the user the whole plan, the expected and worst-case
core-hours, and the job spec, then wait for permission.

The scripts in `scripts/` and the numbers in `references/` come from
benchmarks already run on {{DISPLAY_NAME}} (CP2K 2026.2, October 2026).
**Read `references/results.md` first: the question may already be
answered.** `references/lessons.md` explains why the workflow looks the
way it does.

## What is already measured

| run type | measured | default layout |
|---|---|---|
| AIMD step | H2O-64/128, UiO-66 114 and 456 atoms; 1, 2 and 4 nodes | **16 × 7 per node**, best everywhere |
| single point | UiO-66 456 atoms, H2O-128; 1 node | **16 × 7** (tied with 56 × 2) |
| geometry optimisation | not measured | a step after the first is an ASPC-extrapolated SCF, like an MD step: start from 16 × 7 |

Also measured:
- **Node scaling (456 atoms, 16 × 7):** 1 → 2 → 4 nodes gives 9.34 →
  5.72 → 4.11 s per step, i.e. 82% and 57% efficiency.
- **Spread** between the best and worst layout: 2.0× for DBCSR-bound water,
  1.19× for grid-bound UiO-66.

**Benchmark again only when** the system differs a lot from these (size,
method, hybrid functional, diagonalisation, a new CP2K build), or a long
production run makes a few percent worth the cost.

## Pick the input from its run skill

Benchmark the input that will actually run, shortened:

| production run | build the input with | shorten it to | metric (`parse_bench.py`) |
|---|---|---|---|
| AIMD | `{{SLUG}}-cp2k-aimd` | `STEPS 10`; NVE is fine | s per MD step, mean over steps ≥ 3 |
| single point | `{{SLUG}}-cp2k-single-point` | unchanged | s per single point (detected automatically) |
| geometry optimisation | `{{SLUG}}-cp2k-geo-opt` | `MAX_ITER 3` | whole CP2K time (treated like a single point). It ranks layouts, but is not a per-step time |

Rules:
- Keep every setting identical to production. A different cutoff,
  `EPS_SCF` or functional makes the result meaningless. The bundled water
  inputs (LDA, 280 Ry) overstated the layout effect for production
  settings, 1.30× against 1.15×.
- **Inline or absolute includes.** `run_layout` copies only the input file
  into each run folder, so change `@INCLUDE subsys.inc` to an absolute path
  (`@INCLUDE /home/<user>/.../bench/inputs/subsys.inc`), or build the input
  with `scripts/make_cp2k_md_input.py`, which writes coordinates inline.
- **Test the SCF of any new or bigger cell with a single point first.** An
  unconverged SCF once bought no timing for 153.6 core-hours. AIMD
  benchmark inputs set `IGNORE_CONVERGENCE_FAILURE`, and the gate fails on
  `SCF nc` > 0.
- **Parse-check every input** on the login node:
  `( source ~/software/cp2k/cp2k_env.sh; OMP_NUM_THREADS=1 timeout 60 cp2k.psmp --check -i NAME.inp )`,
  run in a scratch copy so no output lands in the benchmark folder.

Helpers:
- `scripts/make_cp2k_md_input.py STRUCT.xyz --name NAME --out-dir <bench>/inputs`
  writes a 10-step NVE input and a 3-step smoke version from an extended
  XYZ. Run it with `/usr/bin/python3.12 -I` on the login node. Its
  defaults are revPBE-D3, 450/60 Ry and `FULL_SINGLE_INVERSE`; pass
  `--data-dir` if the build isn't `~/software/cp2k/cp2k-2026.2`, and change
  the SCF to `FULL_ALL` for cells of several hundred atoms.
- `scripts/make_sp_inputs.py` turns MD inputs into FULL_ALL single points.
  Edit its `SOURCES` list for the system.

## The benchmark job

One job, one exclusive `mpc` node, three fail-fast stages:

| stage | input | purpose |
|---|---|---|
| 1 smoke | `SMOKE_INPUT` (~3 steps) in 2 layouts | runs at all, binding right, energies agree |
| 2 scan | `SCAN_INPUT` (≥ 10 steps) in `112x1 56x2 28x4 16x7 8x14 4x28` | the timing |
| 3 validate | `VALIDATE_INPUT` (bigger, or `none`) with the 3 best layouts + a repeat of the best | does the ranking hold; noise level |

Each stage stops at its first failed run. The job never starts a run that
could outlast its time limit: a `timeout` per run plus a guard. This
matters because `OverTimeLimit=30` would otherwise let the job run 30
minutes past `--time`.

Set it up on the login node:

```bash
mkdir -p ~/agent/work/cp2k-runs/<system>/bench/inputs
```

`fs_upload` every file from `scripts/` into `bench/`, and the inputs into
`bench/inputs/`. Then edit **only the BENCHMARK SETTINGS block** of
`bench/bench_job.sh`:
- the inputs and the per-run timeouts;
- optionally `SMOKE_LAYOUTS` / `SCAN_LAYOUTS` / `VALIDATE_PICK`;
- `CP2K_ENV` if it isn't `~/software/cp2k/cp2k_env.sh`;
- `BENCH_LIMIT_S`, which must equal the job's duration in seconds.

Timeouts: about 2× the fastest layout's run time. With no timing yet, run
the smoke stage alone first, or scan only 3–4 layouts.

**Dry-run it before submitting.** It runs on the login node with a stub
`srun` that refuses to run unless the stub is first on `PATH`, in a scratch
copy:

```bash
REF_OUT=<finished cp2k.out> SMOKE_INPUT=... SCAN_INPUT=... VALIDATE_INPUT=... \
  bash ~/agent/work/cp2k-runs/<system>/bench/dryrun.sh ~/agent/work/cp2k-runs/<system>/bench <empty scratch dir>
```

Repeat with `FAIL_LAYOUT=16x7` (the stage must stop) and a small
`BENCH_LIMIT_S` such as 400 (the guard must refuse a run). This tests the
stage logic, not CP2K. The stub copies a finished `cp2k.out` from
`REF_OUT`, which is required: use the H2O-32 test output of the build
being benchmarked (the build skill runs that test).
Never run the stage scripts outside `dryrun.sh` on a login node, because a
real `srun` there tries to start a job.

Submit (the script's own `#SBATCH` lines are for manual `sbatch`; under
`submit_job` the spec below sets the resources, and the job runs in
`directory`):

```json
{
  "name": "cp2k-bench-<system>",
  "directory": "/home/<user>/agent/work/cp2k-runs/<system>/bench",
  "executable": "bash bench_job.sh",
  "resources": {"node_count": 1, "processes_per_node": 112, "cpu_cores_per_process": 1,
                "exclusive_node_use": true},
  "attributes": {"queue_name": "mpc", "duration": "00:53:00"}
}
```

- **Cost:** at most 112 cores × 53 min = 98.9 core-hours. Measured jobs took
  5–12 minutes, i.e. 10–22 core-hours.
- **Queue:** an exclusive node is rare (often 0 of 312 idle), so the job
  may wait hours with reason `Resources`. Tell the user.
- No `launcher`: the stage scripts start one `srun` step per layout
  themselves, with `srun --mpi=pmix -n R -c T --cpu-bind=cores
  --distribution=block:block` and `OMP_PLACES=cores OMP_PROC_BIND=close`.
- Don't edit the stage scripts or inputs while the job is pending: the job
  reads them when it starts, not at submission.

## Read the results

The job log (`slurm-<jobid>.out`) ends with the summary. To re-read:

```bash
/usr/bin/python3.12 -I bench/parse_bench.py bench/runs/<jobid>/stage2
```

It reports, per layout:
- s per step (or per single point) and speed-up against 112 × 1;
- core-hours per step and peak memory per rank;
- the main timers: grid, `dbcsr_multiply_generic`, FFT;
- `dE_max` between layouts, and `dF_max` for single points;
- `SCF nc`.

**Look at `SCF nc` before any timing.** Any unconverged SCF after the
first force evaluation makes the step time meaningless.

Interpret, don't just rank:
- **A win under 3% is a tie.** Stage 3's repeat shows the noise (< 1% measured).
- **Where the time goes explains the ranking.** Grid-bound runs favour more
  ranks; DBCSR- or diagonalisation-bound runs favour more threads.
- **Energies across layouts agree to ≤ 1e-6 Ha.** A larger difference is a
  broken run, not noise.
- **Memory:** memory per rank × ranks per NUMA domain stays well under ~16 GB.

### Multi-node follow-up

Only after a single-node scan has passed. `scripts/multinode_layouts_job.sh`:
- runs one input on N nodes in several per-node layouts, baseline first;
- reads the 1-node reference run (`REF_DIR`, `REF_LAYOUT`) to set every
  run's timeout;
- prints speed-up and parallel efficiency against that reference.

Submit it like the job above, with `node_count` N. Dry-run it first with
`JOB=multinode_layouts_job.sh DRY_NODES=N`. Choose the layouts with the
user (each with a reason):
- the 1-node winner (the baseline);
- one with fewer ranks per node (less network traffic);
- one with more ranks per node (wider grid and FFT distribution).

Measured on 2 and 4 nodes: 16 × 7 per node stayed best, and 28 × 4 lost in
DBCSR and FFT.

## Record

Write `bench/results.md` (tables and the recommended layout). Then give
the user the facts worth keeping (system, CP2K version, layout, s/step,
job ID, date) for `references/results.md` in this skill. Pass the chosen
layout back to the production run skill, and project the production cost:
s/step × steps × cores.
