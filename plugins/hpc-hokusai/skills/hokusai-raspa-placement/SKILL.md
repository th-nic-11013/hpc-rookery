---
name: hokusai-raspa-placement
description: Place a fixed number of guest molecules (water) in a rigid framework with RASPA3 fixed-N Monte Carlo on HOKUSAI BigWaterfall2 (HBW2) - write the NVT-MC run folders (N per cell, seeds, cutoff checked against the cell width), run them, check the final configurations and export them as extxyz (TIP4P M site dropped) for MLIP MD or DFT labels. Use whenever the user wants water (or another guest) loaded into a MOF or zeolite at a chosen loading, starting frames with N molecules, "insert N waters", loaded configurations for MD or for an active-learning round - even if they only say "put 15 waters in the primitive cell".
---

# Fixed-N placement with RASPA3 on HOKUSAI BigWaterfall2 (HBW2)

Every cluster tool call uses `facility="hokusai"`. Show the user the run
table (one row per run: N, seed, cutoff, cycles) and the cost, and wait for
permission before any submit.

## Contract

- **Reads:** the framework as extxyz with its cell, its charges (a CP2K
  REPEAT/RESP file for this cell, or a finished run's `force_field.json`
  whose per-type charges carry over), the loadings asked for.
- **Writes:** one run folder per (N, seed) under
  `~/agent/work/raspa/<system>/place/N<n>-s<seed>/` on the cluster; locally
  the same folders with `output/` downloaded, and one extxyz per run.
- **Tools:** `raspa_make_input`, `raspa_parse_output` (the hyogo server;
  local), `structure_check` (yagura; local), `fs_mkdir`, `fs_upload`,
  `render_job_script`, `submit_job`, `get_job_status`, `fs_tail`,
  `fs_download`.
- **Preconditions:** RASPA3 3.0.30 in `software_registry`; the
  user's approval of the run table.
- **Done when:** every run finished, its extxyz passed `structure_check`,
  and the frames are local with their N, seed and source in the header; or
  each failed run is reported with its output lines and dropped.

The model and the keywords are those of hyogo's `raspa_make_input`, checked
against the RASPA3 manual (3.0.30, in the `lit-search-softwares`
library as `raspa3-commands`, `raspa3-restart`) and the 3.0.30 source.
Look a keyword up there (`lit-search search "<keyword>"`), not from memory.

## 1. Decide

- **The cell.** N counts molecules in the whole simulation box. A run in
  the primitive cell (yagura's cell transform, gap G-A) is the cheapest
  frame for DFT; say which cell N refers to in every table.
- **Cutoff vs width.** A cutoff above half the box's smallest
  perpendicular width lets a molecule see its own image; RASPA3 does not
  check it. `raspa_make_input` warns, and its `max_safe_cutoff_A` says the
  limit. For placement a short cutoff (≤ 6 Å in a 12 Å cell) is fine: the
  frames go on to MD and DFT, not to loadings or energies. Replicating the
  cell instead (`unit_cells`) changes what N means.
- **Force field.** The defaults are the UiO-66 water GCMC's (TIP4P/2005,
  UFF, REPEAT charges symmetrised per atom type, LB, shifted LJ, no tail,
  Ewald, 298 K). Keep them for frames that should match the GCMC; any
  change is the user's call.
- **Cycles and seeds.** ~20k production cycles after a short
  initialization is plenty to decorrelate a few tens of waters; give each
  replica its own `seed` (RASPA3 `RandomSeed`; the output prints the seed
  used, check it). Three seeds per N give three independent starts.
- **Is N possible?** Check N against the GCMC/TMMC saturation loading for
  this cell. CBMC insertion of more molecules than fit loops forever
  (manual, `raspa3-faq`).

## 2. Write, stage, run

```text
raspa_make_input(framework_path="prim.extxyz", mode="nvt", out_dir="place/N15-s1",
                 charges_path="<gcmc>/framework/force_field.json", n_molecules=15,
                 cutoff_A=6.0, cycles=20000, init_cycles=2000, equil_cycles=0, seed=1)
```

Read its `warnings` and `types` (every atom typed, net charge ~0). Upload
each folder as it is (`fs_mkdir`, `fs_upload` of its files), then one job
runs the folders side by side, one core each (`OMP_NUM_THREADS=1`;
`raspa3` in each folder, after `source ~/software/mlip/2026.10/raspa3/raspa3_env.sh`): the facility
notes have the script. `render_job_script` (show it), ask, `submit_job`.

## 3. Read and check

Download each folder's `output/` into the local run folder, then:

```text
raspa_parse_output(run_dir="place/N15-s1", extxyz_out_path="place/N15-s1/final.extxyz",
                   config_type="W-N15-s1")
structure_check(path="place/N15-s1/final.extxyz")
```

- `finished`, `seed` (as asked), `n_trace.final` = N, translation and
  rotation acceptance in 0.2–0.8, no energy-drift warning.
- `extxyz.O_H_A` = 0.9572 (the site order is right) and
  `min_guest_framework_A` above ~1.6 Å; `structure_check` passes.
- The extxyz is in RASPA's frame (a along x), with its own cell; the
  next tools take it as it is.

Report the table N × seed with these numbers. A run that fails a check is
reported and dropped, not patched.

## HOKUSAI notes

- **Build:** RASPA3 3.0.30, the prebuilt el8 skylake-avx512 RPM unpacked
  under `~/software/mlip/2026.10/raspa3` with an MKL shim for
  libblas/liblapack (`software_registry` `raspa3-3.0.30-other-cpu-prebuilt`,
  jobs 9820144/9820145). `source ~/software/mlip/2026.10/raspa3/raspa3_env.sh`
  before `raspa3`.
- **Layout:** partition `mpc`, one core per run folder; several folders in
  one job, side by side (a placement run in a 114-atom cell with ≤ 30 waters
  takes minutes; 15 runs ≈ 1 core-h, an estimate, not yet measured):

```bash
source ~/software/mlip/2026.10/raspa3/raspa3_env.sh
export OMP_NUM_THREADS=1
for d in N*-s*/; do ( cd "$d" && raspa3 > raspa3.stdout 2> raspa3.stderr; echo "$d exit $?" ) & done
wait
grep -L "Simulation finished" N*-s*/output/output_*.txt   # unfinished runs, if any
```

  Request one core per folder (`ntasks=1`, `cpus_per_task` = folders),
  1 GB per core and 1 h.
