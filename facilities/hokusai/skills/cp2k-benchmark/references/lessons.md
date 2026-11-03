# How the benchmark design came about (2026-10-16 session)

> From the rccs-agentic-ai `hokusai-hpc-cp2k-benchmark` skill, where these
> benchmarks were run. Paths such as `~/software/cp2k/bench/...` are on HOKUSAI;
> the scripts it names are in `../scripts/` here, except `geomcheck.py`
> (`hokusai-structure-making`). Other rccs-agentic-ai skills it names map to
> `hokusai-cp2k-build`, `hokusai-submitting-jobs` and `hokusai-reference`.

The reasons behind the rules in SKILL.md, in the order they were learned.

1. **Goal and budget.** The user wanted the best ranks x threads layout on one MPC
   node for CP2K 2026.2 (`~/software/cp2k/cp2k-2026.2`, GCC 13.2 + Open MPI 4.1.6 +
   MKL), standard H2O inputs, at most about 100 mpc core-hours. The only earlier
   timing was H2O-32 on 2 nodes x 4 ranks x 2 threads: 28.3 s, 1.31 s per steady
   MD step, first step 13.8 s.
2. **Three separate jobs, then a chain, then one job.** First version: smoke,
   scan and validation as three `sbatch` scripts submitted by hand. The user asked
   for automatic gating, so a driver submitted an `afterok` chain with
   `--kill-on-invalid-dep=yes`, and each failing stage cancelled the later ones by
   job ID from a chain file, because HOKUSAI does not cancel dependents of a
   failed job. The user then asked for a single Slurm script with the stages as
   bash scripts: one submission, one queue wait, no orphaned dependents. That is
   the current design. Cost of the trade: the smoke stage now runs on a whole
   node, so it uses H2O-32 (or a 3-step input) to stay short.
3. **Cost cap.** `OverTimeLimit=30` lets a job run 30 min past `--time`, so the
   worst case is not 112 x `--time` unless the job stops itself. Hence `timeout`
   per run and the guard in `run_layout` that refuses a run when less than
   timeout + 90 s of job time is left. 53 min keeps the hard maximum at
   98.9 core-hours.
4. **Fail fast.** Each stage stops at its first failed run instead of finishing
   the scan, to save budget. A run counts as failed if srun exits non-zero, the
   timeout fires, or `PROGRAM ENDED` is missing. The `--check` gate also needs
   all energies to agree; the user loosened the tolerance from 1e-8 to 1e-6 Ha
   after UiO-66 (EPS_SCF 1e-6) was added.
5. **Dry-run accident.** A dry run was started before its stub `srun` existed
   (the command creating it had been blocked), so the stage scripts called the
   real `srun` on the front end. Slurm's cli_filter rejected it ("please specify
   partition option") and no job was created, but it could have been. Since then
   the dry-run harness checks `command -v srun` against the stub and aborts
   otherwise, and it works in a scratch copy so test output never mixes with
   real `runs/`.
6. **Pending jobs read current files.** A job reads its stage scripts and inputs
   when it starts. An early smoke job was still queued when the helper
   changed incompatibly; it was cancelled. Later changes kept the defaults
   backward compatible for the queued layout-scan job.
7. **Queue reality.** At submission no MPC node was fully idle (217 mixed, 94
   allocated), so the exclusive job waited with `Resources` and no start
   estimate.
8. **Own system.** The user wanted a Zr MOF for CO2 capture as a second
   benchmark. UiO-66 was chosen (rigid, closed shell, Zr q12 and 450 Ry load the
   grids differently from water). The user built the ordered primitive cell
   themselves and asked for revPBE-D3 / DZVP-MOLOPT-SR-GTH / GTH; see
   `structures.md`.
9. **First results (2026-10-17).** All single-node jobs passed; 16 x 7 won for
   water and UiO-66. The UiO-66 single-node job ran twice (submitted once alone,
   once in the dependency pair), which gave a free noise check (< 1 %). The
   dependent 4-node job started as planned, but its SCF never converged: the
   inputs' IGNORE_CONVERGENCE_FAILURE let it "pass" every gate that existed, and
   153.6 core-hours bought no timing. Since then `parse_bench.py` counts
   unconverged SCF runs after the first force evaluation and the gate fails on
   any. Lesson: before scaling a system up (more atoms or more nodes), confirm the
   SCF settings converge for the bigger cell with a cheap single point.
10. **The SCF tests (2026-10-18).** Five single points on one node (12.7
    core-hours) answered what 153.6 core-hours of 4-node MD could not: the
    456-atom cell has the same gap and energy per unit as the 114-atom cell,
    and converges with every changed setting (OT CG, FULL_ALL, or
    diagonalisation). Only OT DIIS + FULL_SINGLE_INVERSE, the generator's
    default, stalled on the big cell. Prefer FULL_ALL (with ENERGY_GAP) for
    cells of several hundred atoms, and test the SCF of any new, bigger cell with
    a single point before an MD benchmark.

11. **Matched DFT settings and slow starts (2026-10-19).** The bundled water inputs
    (LDA, TZV2P, 280 Ry) cannot be compared with a revPBE-D3/DZVP/450 Ry system:
    build comparison inputs from the system's own input as a template
    (`make_water_inputs.py` in the bench folder). The first matched-water job
    lost its smoke run: srun-to-CP2K start took 56 s (4 s on 10-17; 85 s
    in another job that day), then CP2K stalled before the SCF with near-zero CPU,
    and the 180 s timeout killed it. The same input runs serially in 14 s; two of
    four serial front-end tries also stalled, so suspect file-system stalls. Diagnose
    with `PROGRAM STARTED AT` in cp2k.out against the run start, `sacct` TotalCPU of
    the step, and file mtimes: CP2K output is buffered, so "banner only" does not
    mean CP2K stopped at the banner. Give each run at least 2 min of launch slack
    above its expected time. Also: a job's summary must not overwrite the global
    results.csv (`parse_bench.py` now writes it only without arguments). Dry runs of
    a folder whose scripts hard-code its path must rewrite those paths in the copies
    (`dryrun.sh` now does).
12. **Multi-node layout test (2026-10-21, prepared).** After 1 -> 4 nodes gave only
    57 % for the 456-atom UiO-66 cell, the user asked whether another per-node
    layout scales better. Added `scripts/multinode_layouts_job.sh` (baseline first,
    later failures recorded, speed-up against a 1-node reference) and `JOB=` mode
    in `dryrun.sh`. Chosen: 16x7 (baseline repeat), 8x14 (fewer ranks), 28x4 (more
    ranks); left out 56x2, 4x28 and pure MPI. First plan cut MD steps 10 -> 6 to
    save core time; the 4-node log showed that saves ~16 s of a 155 s run (start-up
    ~60 s and the first step 57 s dominate), so the 10-step input was kept. The dry
    run caught that a failed non-baseline layout left exit 0; the job now ends with
    exit 1 listing the layouts that did not finish. The user then dropped the 16x7
    repeat to save a run (~20 core-h): only 8x14 and 28x4, compared with 64x7 of
    the 2026-10-19 4-node run (other nodes). Added `STOP_ON_FIRST` and `CMP_DIR`/`CMP_LAYOUT`.
13. **Single points are a different workload (2026-10-27).** A single point
    converges from the atomic guess (~55 OT steps for 456-atom UiO-66 with
    FULL_ALL), like the first MD step, where 8x14 per node beat 16x7 on 4 nodes;
    so the AIMD winner must not be assumed for single-point work. CP2K 2026.2
    prints forces to the main output as `FORCES|` lines (atom, x, y, z, |f|), not
    the older `ATOMIC FORCES in [a.u.]` block (that format now only goes to
    separate files); `parse_bench.py` reads both. Leave out
    IGNORE_CONVERGENCE_FAILURE in single-point inputs so an unconverged SCF fails
    the run instead of producing a meaningless time.
