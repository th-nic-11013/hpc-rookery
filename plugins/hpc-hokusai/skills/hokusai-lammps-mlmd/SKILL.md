---
name: hokusai-lammps-mlmd
description: Run molecular dynamics with a machine-learned potential (MACE through LAMMPS pair_style metatomic) on HOKUSAI BigWaterfall2 (HBW2) - export a fine-tuned model to a metatomic .pt, write NVT/NPT/NVE inputs (pressure holds, compression and decompression ramps, D3, PLUMED), submit with the measured layout, read the log, and turn the dumped frames into checked extxyz for analysis or the next active-learning round. Use whenever the user wants MLMD, MLIP MD, MACE MD, LAMMPS with MACE, an exploration or stability run of a fine-tuned model, a pressure or temperature scan, or frames for an active-learning pool - even if they only say "run the model at 600 K".
---

# MLMD with LAMMPS on HOKUSAI BigWaterfall2 (HBW2)

Every cluster tool call uses `facility="hokusai"`. Show the user the run
table (one row per run: ensemble, T, P or ramp, steps, seed, start
structure) and the cost, and wait for permission before any submit.

## Contract

- **Reads:** the model (a metatomic `.pt` on the cluster, or a MACE
  `.model` to export first), a start structure, `software_registry` and
  `benchmarks` (`md_step`) through `launch_plan`.
- **Writes:** one run folder per run under `~/agent/work/mlmd-<system>/`
  on the cluster (`start.data`, `in.<mode>.lmp`, `log.lammps`,
  `frames.dump` or `traj.xyz`, restart files); locally the logs and dumps
  you download, and the extxyz made from them.
- **Tools:** `structure_to_lammps_data`, `structure_to_extxyz`,
  `framework_check`, `trajectory_monitor` (the yagura server; local),
  `lammps_make_input`, `lammps_parse_log`, `plumed_make_input` (the hyogo
  server; local), `launch_plan`, `fs_ls`, `fs_mkdir`, `fs_upload`,
  `render_job_script`, `submit_job`, `get_job_status`, `fs_tail`,
  `fs_download`, `records_append`.
- **Preconditions:** a LAMMPS-metatomic build in `software_registry`
  (`hokusai-records`); the user's approval of the run table and cost.
- **Done when:** every run ended (log finished, no `ERROR`, finite thermo),
  the requested analysis is reported, and the frames a later step needs
  are local as checked extxyz; or each failure is reported with its log
  lines and no number quoted from it.

## 1. The model: export once per model

LAMMPS reads a metatomic `.pt`, not a MACE `.model`. Foundation models
already have one (the `lammps` entry of the MACE pack's model table). A
fine-tune needs exporting once, in its own folder
`~/agent/work/mlmd-<system>/export-<model>/`, on a CPU node of
`mpc` (a short job, a few minutes), with the scripts
beside this skill:

1. `python select_head_cpu.py <fine-tune>.model Default <name>-default.model`:
   a multihead fine-tune lists `pt_head` (the replay head) too; LAMMPS
   needs the one head the system was trained on (`Default`). Plain
   `mace.cli.select_head` fails on a CPU node for a GPU-trained model
   (e3nn pieces pickled with CUDA tensors); this script loads them on CPU.
2. `mtt train options-f32.yaml -o <name>-f32.pt` with `SINGLE_HEAD_MODEL`
   replaced and a small `dummy_dataset.xyz` (any extxyz with `energy`)
   in the folder: 0 epochs, it only exports. Float32 is enough for MD and
   ~1.7x faster than float64.
3. `python check_export.py --frames <labels>.extxyz --ref <model>.predictions.json --pt <name>-f32.pt --single <name>-default.model`:
   compares with the model's own float64 predictions (from
   `hokusai-mace-eval`'s `predict.py`) on 3 frames. A float32 export
   shifts every energy by a near-constant offset; the gate is the spread
   of that offset (< 0.1 meV/atom) and the largest force difference
   (< 5 meV/Å). Exit 1 means stop: no MD on a model that failed it.

Record the three sha256 sums (fine-tune, single head, `.pt`) in the run's
notes; every MD result quotes the `.pt` it used.

## 2. Start structure and input

```text
structure_to_lammps_data(source_path="<relaxed or equilibrated structure>", out_path="start.data")
lammps_make_input(data=<the whole result above>, model="<.pt path on the cluster>",
                  mode="npt", steps={"run": 40000}, temperature_k=300, pressure_bar=15000,
                  seed=<own seed per run>, dump_every=100, restart_every=2000,
                  out_path="<local run folder>/in.npt.lmp")
```

- **A new model or system starts with a smoke run:** `mode="valid"`
  (NVT then NVE, short) or a 10 ps hold at the mildest condition. Check
  the step-0 energy against the model's ASE prediction and the NVE drift
  before the production matrix.
- **Pressure scans:** a hold is `pressure_bar` alone; a compression or
  decompression ramp adds `pressure_end_bar` (the target moves linearly
  over the run). A ramp puts a transition at a rough pressure (it
  overshoots at a finite rate); holds at a few pressures around it pin it.
  A decompression starts from the compressed run's restart
  (`restart_from`).
- **Frames:** `npt` dumps `frames.dump` with each frame's box (the cell
  is what changes); `nvt`/`nve` dump `traj.xyz`. 50 fs (100 steps at
  0.5 fs) is the stride active-learning pools have used.
- **Seeds:** every run its own seed, written in the run table. A held-out
  test set comes from a whole run with its own seed, never from frames
  next to training picks.
- **Dispersion:** a model trained on D3-free labels (MatPES, MP, a
  fine-tune on D3-free labels) runs with `dispersion` when the physics
  needs it; say which, since it changes the pressure the cell feels.
- **Bias:** `plumed_make_input` writes the PLUMED file; pass its name as
  `plumed_path`.
- Read every warning `lammps_make_input` returns.

## 3. Layout, cost and submit

```text
launch_plan(facility="hokusai", code="lammps", run_type="md_step",
            system={"atoms": <n>}, steps=<total steps>)
```

Quote `estimate` and `basis.match`; with no `md_step` record for a
similar system, say the walltime is a guess and keep the first run short.
One run per folder: `fs_ls` the folder first, it must not exist or be
empty (two sessions once submitted the same run into one folder 35 s
apart). Then `fs_mkdir`, `fs_upload` `start.data`, the input and (if
any) the PLUMED file, merge `launch_plan`'s `job_spec` into the spec,
`render_job_script` (show it), `submit_job`. The run script should end
by printing `grep -c "ITEM: TIMESTEP" frames.dump` and the `Loop time`
line, so `fs_tail` of the job output answers "did it finish" alone.

## 4. Read the run

`get_job_status`; when it ends, `fs_download` `log.lammps` only (the
dump can be large: download it when the analysis needs it), then
`lammps_parse_log(log_path=...)`: finished, errors, non-finite thermo,
s/step and ns/day, T range, and `tail` (second-half mean, std and block
means of T, P, volume, density, PE) with `target_P`. Quote averages from
`tail`, never from the last thermo line. Block means that walk one way
(the "volume moved" warning) mean not equilibrated or a transition under
way: report it as that, not as an equilibrium volume. A completed run with
its timing gets one `benchmarks` record (`records_append`,
`run_type: md_step`, the layout, atoms and s/step), so the next
`launch_plan` is exact.

## 5. Frames, checks and the next round

```text
structure_to_extxyz(source_path="frames.dump", specorder=[<elements in type order>],
                    dt_fs=<timestep x dump stride>, config_type="<run>", out_path="<run>.extxyz")
framework_check(path="<run>.extxyz", reference_path="<the start structure>", dt_fs=<same>)
trajectory_monitor(path="<run>.extxyz", log_paths=["log.lammps"], out_dir="<run>-monitor")
```

`framework_check` flags broken bonds, metal-oxygen losses, overlaps and
volume jumps per frame (hard frames are not training data); 
`trajectory_monitor` finds the events (bond openings, proton transfers,
collapse onset) and the frames around them. Report events with their
time and run, not just counts.

For an active-learning round the extxyz files are the pool: score them
with a committee (`hokusai-mace-eval`'s `predict.py` per member, then
`mlip_committee`), pick with `mlip_select_frames`, and label the picks
(`hokusai-campaign`). The user confirms the pick before labelling.

## HOKUSAI notes

- **Builds:** `~/software/mlip/2026.10/lammps-cpu/bin/lmp_mpi` and, with
  D3 (EXTRA-PAIR), `lammps-cpu-d3/bin/lmp_mpi`; both take
  `source ~/software/mlip/2026.10/mlip_env.sh`. The D3 build runs plain
  metatomic inputs too, so round-2/3 used it for everything. Exports
  (`mtt`, `mace`) run in the same environment.
- **Layout:** partition `mpc`, 1 rank x 56 threads, bound to cores
  (`srun --mpi=pmix --ntasks=1 --cpus-per-task=56 --cpu-bind=cores`,
  `OMP_PLACES=cores OMP_PROC_BIND=close PMIX_MCA_psec=native`); a run uses
  half a node, so two runs share one.
- **Measured speed:** UiO-66 primitive (456 atoms), round-2 fine-tune,
  float32: 0.20-0.33 s/step at 0.5 fs (static holds faster, collapsed
  cells slower): 40,000 steps (20 ps) fit in a 7 h request, 100,000
  steps in ~10 h. Eight runs (round 3 step A, jobs 9922244-9922251) cost
  ~33 node-h. Float64 is ~1.7x slower.
- **Export job:** select_head_cpu + `mtt train` + check on one `mpc`
  node took minutes (job 9922229); request 30 min and 16 GB.
- **PLUMED:** `lmp_mpi` links `libplumed.so` directly, so
  `PLUMED_KERNEL` is ignored; for the OPES build put
  `LD_LIBRARY_PATH=${MLIP_STACK}/plumed-opes/lib` in front (jobs
  9748509, 9749277).
- **Run script tail** (so `fs_tail` answers "did it finish"):

```bash
srun ... "${LMP}" -in in.npt.lmp -log log.lammps -screen none 2> srun.err
rc=$?; grep -q "^ERROR" log.lammps && rc=1
echo "--- frames: $(grep -c "ITEM: TIMESTEP" frames.dump 2>/dev/null)"
grep -E "^ERROR|^Loop time|Performance" log.lammps
exit ${rc}
```

- **Known model behaviour (UiO-66, not a LAMMPS fault):** MACE-MP-0+D3
  and the round-1/2 fine-tunes collapse the framework under pressure
  (round 2: between 1.0 and 1.25 GPa at 300 K, not reversed within 30 ps
  of decompression). A collapse in a new run is a result to report and
  sample, not a crash to retry.
