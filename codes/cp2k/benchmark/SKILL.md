---
name: {{SLUG}}-cp2k-benchmark
description: Benchmark CP2K on {{DISPLAY_NAME}} - find the fastest MPI-rank x OpenMP-thread layout and node count for a system and run type (AIMD, single point, geometry optimisation), first from stored benchmark records and otherwise with a fail-fast, cost-capped single Slurm job that is dry-run safely on the login node; record the timings. Use whenever the user wants to benchmark, time, profile, scale-test or tune CP2K on {{DISPLAY_NAME}}, asks how many nodes, ranks or threads to use, asks why a CP2K run is slow, or is about to start a long CP2K run.
---

# Benchmarking CP2K on {{DISPLAY_NAME}}

Every tool call uses `facility="{{SLUG}}"`. A benchmark spends real core
time. Show the user the whole plan, the expected and worst-case
core-hours, and the job spec, then wait for permission.

This file is the method; it holds for CP2K on any Slurm machine.
**{{DISPLAY_NAME}} values** (partition, node shape, scan layouts, binding
line, front-end Python, job spec, cost) are in `references/facility.md`.
Measured timings are `benchmarks` records, read and written through
`{{SLUG}}-records`. `references/lessons.md` explains why the workflow
looks the way it does.

## Contract

- **Reads:**
  - `benchmarks`: earlier timings;
  - `software_registry`: which build to time (`default: true` unless the
    user names one).
- **Writes:** `benchmarks`, one record per layout × node count, and
  `benchmarks_provenance` with the job IDs (`cp2k_bench_records`).
- **Tools:**
  - `fs_upload`, `cp2k_bench_dryrun`, `cp2k_check_input`,
    `cp2k_bench_parse` and `fs_view` / `fs_tail` on the login node;
  - `submit_job` and `get_job_status` for the job;
  - `cp2k_bench_input` (reads the build's data folder, writes locally),
    `structure_check` and `structure_stretch_xh` for the structure;
  - `cp2k_bench_records`, `launch_plan` and `{{SLUG}}-records` locally.
- **Preconditions:**
  - a build (`{{SLUG}}-cp2k-build`);
  - the production input, written by its run skill;
  - for a new or bigger cell, a converged single point;
  - the user's approval of the cost.
- **Done when:**
  - the records are appended (including failed runs, as `valid: false`);
  - a layout and node count, with the measured s/step, are handed back to
    the run skill;
  - the production cost is projected.

## 1. Look for an existing measurement

Ask before spending anything. `launch_plan` does the lookup:

```text
launch_plan(facility="{{SLUG}}", code="cp2k", run_type="<aimd_step|single_point|geo_opt>",
            system={"label": "<label>", "atoms": <atoms>, "electrons": <if known>},
            method={"functional": "<functional>", "hybrid": <true|false>})
```

`basis.match` answers it:
- `"exact"`: this system with this method was timed. No benchmark needed.
- `"similar"`: a system within 30 % in size with the same hybrid flag.
  `launch_plan` checks nothing else, so check the record (`basis.method`,
  `basis.record_ids`) against the rules below before relying on it.
- `"none"`: benchmark, unless the job is short enough that a guess costs
  less than the benchmark.

A record answers the question when **all** of these hold:
- the method is the same: functional, hybrid or not, basis family,
  cutoff within ~10%, and the same kind of SCF (OT or diagonalisation);
- the system is similar: atom count within ~2×, a similar composition
  class (water, MOF, oxide...), and k-points;
- the build is the same CP2K version, or one close to it.

For the details `launch_plan` doesn't return, query the records directly
(`records_query(..., record_type="benchmarks", filters={"code": "cp2k",
"system.label": "<label>", "valid": true})`, see `{{SLUG}}-records`).

On a match, `launch_plan`'s layout is the fastest valid one at the chosen
node count; layouts within 3% of it (`basis.options`, or the records) are
ties.

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
| AIMD | `{{SLUG}}-cp2k-aimd` | `STEPS 10`; NVE is fine | s per MD step, mean over steps ≥ 3 |
| single point | `{{SLUG}}-cp2k-single-point` | unchanged | s per single point (detected automatically) |
| geometry optimisation | `{{SLUG}}-cp2k-geo-opt` | `MAX_ITER 3` | whole CP2K time (treated like a single point). It ranks layouts, but is not a per-step time |

Rules:
- **Keep every setting identical to production.** A different cutoff,
  `EPS_SCF` or functional makes the result meaningless. Bundled
  low-cutoff test inputs overstate how much the layout matters.
- **Inline or absolute includes.** `run_layout` copies only the input file
  into each run folder. Change `@INCLUDE subsys.inc` to an absolute path, or
  build the input with `cp2k_bench_input`, which writes the coordinates
  inline.
- **Test the SCF of any new or bigger cell with a single point first.** An
  unconverged SCF once bought no timing for a 4-node job (lessons 9–10).
  AIMD benchmark inputs set `IGNORE_CONVERGENCE_FAILURE`, and the gate
  fails on `SCF nc` > 0.
- **Parse-check every input** on the login node, in a scratch copy, using
  the command in `references/facility.md`.

For an AIMD benchmark of a structure, write the inputs with the tool, on
this machine, from the extended XYZ:
1. `structure_check` the XYZ. If it reports `short_xh` (X-ray C-H bonds
   near 0.95 Å), run `structure_stretch_xh` first and use its output.
2. Write the 10-step NVE input and its 3-step smoke version:

   ```text
   cp2k_bench_input(facility="{{SLUG}}", xyz_path="<local>.xyz", name="<label>",
                    out_dir="<local bench>/inputs", build_id=<omit for the default>)
   ```

   - Defaults: revPBE-D3 (zero damping), 450/60 Ry, Gamma point,
     OT/DIIS with `FULL_SINGLE_INVERSE`. Set `xc`, `d3`, `cutoff`,
     `rel_cutoff`, `charge` and `steps` to match production.
   - q and the GTH-PBE potential come from the build's own data folder,
     the same lookup as `cp2k_kinds`; an element it lacks stops the tool.
   - Read its `warnings` to the user: an odd electron count needs UKS
     and `MULTIPLICITY` added by hand; a cell under 10 Å wide samples
     Gamma poorly.
   - Change `PRECONDITIONER` to `FULL_ALL` for cells of several hundred
     atoms.
3. `fs_upload` both files into `bench/inputs/`.

By hand, without the tool (run with `{{CP2K_PYTHON}} -I` on the login
node):
- `scripts/make_cp2k_md_input.py STRUCT.xyz --name NAME --out-dir <bench>/inputs
  --data-dir <the build's data_dir>` writes the same two inputs. Its
  `--stretch-ch 1.08` stands in for `structure_stretch_xh`.
- `scripts/make_sp_inputs.py` turns MD inputs into FULL_ALL single points.
  Edit its `SOURCES` list for the system.

## 3. The benchmark job

One job on one exclusive node, with three fail-fast stages:

| stage | input | purpose |
|---|---|---|
| 1 smoke | `SMOKE_INPUT` (~3 steps) in 2 layouts | it runs at all, the binding is right, the energies agree |
| 2 scan | `SCAN_INPUT` (≥ 10 steps) in `{{CP2K_SCAN_LAYOUTS}}` | the timing |
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

**Dry-run it before submitting.** `cp2k_bench_dryrun` runs the folder's
`dryrun.sh` on the login node, in a scratch folder of its own, with a stub
`srun`; the harness refuses to run unless that stub is first on `PATH`.
Pass the BENCHMARK SETTINGS as `settings`:

```text
cp2k_bench_dryrun(facility="{{SLUG}}", bench_dir="~/agent/work/cp2k-runs/<system>/bench",
                  ref_out="<finished cp2k.out>",
                  settings={"SMOKE_INPUT": "...", "SCAN_INPUT": "...", "VALIDATE_INPUT": "...",
                            "SMOKE_TIMEOUT": "...", "SCAN_TIMEOUT": "...", "VALIDATE_TIMEOUT": "..."})
```

One call runs three cases, and `ok` is true only if each behaved:
- `pass`: all stages pass;
- `fail_layout`: the stub fails one scan layout (`fail_layout`, default the
  last one not in the smoke stage), and the stage must stop there;
- `time_limit`: a small `BENCH_LIMIT_S` (`time_limit_s`, default 60), and
  the guard must refuse a run.

A case with `expected: false` says why; read its `log_tail`. For the
multi-node job, pass `job_script="multinode_layouts_job.sh"`, `nodes` and
its settings (`LAYOUTS`, `REF_DIR`, `REF_LAYOUT`, `CP2K_ENV`).

This tests the stage logic, not CP2K. The stub copies a finished `cp2k.out`
from `ref_out`, which is required: use the H2O-32 test output of the build
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

The job log (`slurm-<jobid>.out`) ends with the summary table. To re-read
the results as data:

```text
cp2k_bench_parse(facility="{{SLUG}}",
                 stage_dirs=["~/agent/work/cp2k-runs/<system>/bench/runs/<jobid>/stage2"])
```

Per stage it returns `passed` (the gate the job used) and `ranked` (usable
full-node layouts, fastest first); per run it reports:
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
`cp2k_bench_dryrun(..., job_script="multinode_layouts_job.sh", nodes=N)`.
Choose the layouts with the user, each with a reason:
- the 1-node winner (the baseline);
- one with fewer ranks per node (less network traffic);
- one with more ranks per node (wider grid and FFT distribution).

Query `benchmarks` for the same system at other node counts first. They
may already show where the efficiency drops.

## 5. Record

`cp2k_bench_records` writes the `benchmarks` records: one per layout of
one input at one node count, repeats averaged. Failed, killed, unconverged
or disagreeing runs get `valid: false` and a `reason`; the job IDs go to
`benchmarks_provenance`, not into the records. Pass the `cp2k_bench_parse`
result from step 4, or the stage folders:

```text
cp2k_bench_records(facility="{{SLUG}}", run_type="<aimd_step|single_point|geo_opt>",
                   stage_dirs=["~/agent/work/cp2k-runs/<system>/bench/runs/<jobid>/stage2"],
                   input_name="<the scan input>", nodes=<N>,
                   system={"label": ..., "atoms": ..., "elements": {...}, "periodic": "xyz",
                           "kpoints": "gamma", "electrons": ..., "cell_volume_A3": ...},
                   method={"functional": ..., "dispersion": ..., "basis": ..., "cutoff_ry": ...,
                           "rel_cutoff_ry": ..., "hybrid": ..., "scf": "<the SCF as used>"})
```

- **The system and method** come from the structure and the input, not
  from the folder: label, atoms and elements; functional, basis,
  cutoffs and the SCF exactly as the input sets them.
- **One input per call.** Stage 3 times a different system: record it in
  its own call, with its own `system`. Smoke runs are too short to keep.
- The build is the default one unless you pass `build_id`; `partition` and
  `node_type` come from the facility.
- An id that already exists stops the call and writes nothing. For the same
  system timed with another method on the same day, pass a `tag`.
- It refuses dry-run results.

The result's `check_errors` is `records_check` afterwards: it must be
empty. `extra.parallel_efficiency` and `extra.core_hours` are not filled
in. Keep `bench/results.md` in the benchmark folder for the full tables,
if the user wants it. Records hold the numbers other skills use.

Hand the chosen layout and node count back to the production run skill,
and project the production cost: s/step × steps × nodes × cores per node ÷
3600 core-hours. Offer to share the new records (`{{SLUG}}-records`,
"Sharing a benchmark"), but don't do it unasked.
