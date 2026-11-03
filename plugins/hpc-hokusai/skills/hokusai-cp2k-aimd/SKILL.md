---
name: hokusai-cp2k-aimd
description: Run CP2K ab initio (Born-Oppenheimer) molecular dynamics on HOKUSAI BigWaterfall2 (HBW2) - NVT equilibration and NVE/NVT production, cost estimates from measured step times, restarts across the 24-hour limit, and checks on temperature, energy conservation and SCF. Use whenever the user wants AIMD, BOMD, first-principles MD, a trajectory, or to continue or restart an MD run with CP2K on HOKUSAI BigWaterfall2 (HBW2).
---

# CP2K AIMD on HOKUSAI BigWaterfall2 (HBW2)

Every tool call uses `facility="hokusai"`. Show the user the key settings
and the job spec, state the cost, and wait for permission before
submitting. AIMD is usually the most expensive thing done with CP2K, so
give the core-hour estimate before every submission.

**Prerequisites:**
- a CP2K build (`~/software/cp2k/cp2k_env.sh` by default; `hokusai-cp2k-build`);
- `subsys.inc` (`hokusai-structure-making`), ideally relaxed first
  (`hokusai-cp2k-geo-opt`). X-ray C-H bonds (~0.95 Å) put large forces on
  the first steps;
- a converged single point on the structure (`hokusai-cp2k-single-point`);
- for anything long, a layout from `hokusai-cp2k-benchmark` measured on
  this system. Thousands of steps multiply any per-step loss.

## 1. Plan with the user

| setting | template | guidance |
|---|---|---|
| ensemble | NVT, CSVR | CSVR is robust far from equilibrium and for small systems. Production in NVT, or NVE for dynamical properties |
| `TIMECON` | 100 fs | ~50 fs for fast heating, 200–1000 fs for gentle production. **Never shrink it to a few fs, or use `TEMP_TOL` rescaling, to "fix" temperature fluctuations**: that destroys canonical sampling |
| `TIMESTEP` | 0.5 fs | 0.5 fs with H present; 1–2 fs without. Validate by energy conservation |
| `TEMPERATURE` | 300 K | the user's choice |
| `STEPS` | 2000 (1 ps) | steps **in this job** (see section 4) |
| `EPS_SCF` | 1e-6 | forces need it. A looser SCF shows up as conserved-quantity drift |

**Estimate the cost from measured step times** (16 × 7 per node, revPBE-D3,
450 Ry; `hokusai-cp2k-benchmark` `references/results.md`):

| system | nodes | s per steady step | core-h per ps (0.5 fs) |
|---|---|---|---|
| H2O-64 (192 atoms) | 1 | 1.80 | ~112 |
| UiO-66 primitive (114 atoms) | 1 | 5.14 | ~320 |
| UiO-66 conventional (456 atoms) | 1 / 2 / 4 | 9.34 / 5.72 / 4.11 | ~580 / ~710 / ~1020 |

- H2O-64 and the 114-atom cell were timed with `FULL_SINGLE_INVERSE`, and
  the 456-atom cell with `FULL_ALL`, the template's choice. `FULL_ALL`
  preconditioner builds took 16% of the 456-atom step time, so expect small
  cells to run a little slower than listed with the template as written.
- The first step costs 7–10 steady steps (SCF from the atomic guess).
- For a new system, run 20–50 steps first. Read `UsedTime` (the last
  column of `<PROJECT>-1.ener`) or `MD| CPU time per MD step`, skip the
  first few steps, and multiply.
- Tell the user the total core-hours and how many 24 h jobs it means.

## 2. Write the input

Start from `templates/aimd_nvt.inp`. It has the single-point DFT block
(revPBE-D3, DZVP-MOLOPT-SR-GTH, 450/60 Ry, OT DIIS + FULL_ALL) plus:
- ASPC extrapolation (order 3), which makes later steps several times cheaper;
- trajectory and velocities every step;
- a restart file every 10 steps;
- `WALLTIME` = the job's duration minus ~15 minutes (`24:00:00` → `85500`),
  so CP2K stops cleanly with a fresh restart file.

`FULL_ALL` converged every cell tested on HOKUSAI BigWaterfall2 (HBW2).
`FULL_SINGLE_INVERSE` is slightly cheaper for ~100-atom cells, but it
stalled on a 456-atom cell.

**NVE production:** set `ENSEMBLE NVE` and delete `&THERMOSTAT`. Start it
from the equilibrated run's restart (section 4) so it inherits the
velocities.

Parse-check on the login node:
`( source ~/software/cp2k/cp2k_env.sh; cd <run dir>; OMP_NUM_THREADS=1 timeout 60 cp2k.psmp --check -i aimd_nvt.inp )`.

## 3. Stage and submit

Stage the input and `subsys.inc` in `~/agent/work/cp2k-runs/<system>/md/`.

```json
{
  "name": "cp2k-md-<system>",
  "directory": "/home/<user>/agent/work/cp2k-runs/<system>/md",
  "pre_launch": "source $HOME/software/cp2k/cp2k_env.sh",
  "launcher": "srun --mpi=pmix --cpus-per-task=7 --cpu-bind=cores --distribution=block:block",
  "executable": "cp2k.psmp -i aimd_nvt.inp -o aimd_nvt.out",
  "environment": {"OMP_NUM_THREADS": "7", "OMP_PLACES": "cores", "OMP_PROC_BIND": "close"},
  "resources": {"node_count": 1, "processes_per_node": 16, "cpu_cores_per_process": 7},
  "attributes": {"queue_name": "mpc", "duration": "24:00:00"}
}
```

The layout and binding rules are in `hokusai-cp2k-single-point`. 16 × 7
per node is the measured best for AIMD on 1, 2 and 4 nodes. More nodes
buy wall time at a falling efficiency (456 atoms: 82% on 2 nodes, 57% on
4).

Wall time: `mpc` allows 24 h. `mpc_l` allows 72 h, but only 8 nodes, and
long jobs find backfill gaps less easily. Prefer 24 h segments on `mpc`.

## 4. Continue a run

Each continuation is a copy of the input with `SCF_GUESS RESTART` in
`&SCF`, plus, at the top level:

```text
&EXT_RESTART
  RESTART_FILE_NAME <PROJECT>-1.restart
&END EXT_RESTART
```

This restores positions, velocities, thermostat state and the step
counter. Verified on CP2K 2026.2:
- **`STEPS` then counts additional steps.** A continuation from step 4
  with `STEPS 3` ran steps 5–7. Set `STEPS` to the number of steps this
  segment should run.
- **`<PROJECT>-1.ener`, `-pos-1.xyz` and `-vel-1.xyz` are appended to**,
  so the run stays one set of files.

Write each segment's log to a new file (`-o aimd_nvt_2.out`). Changing
`TIMECON` between equilibration and production (100 → 200 fs) is normal.

Chaining segments: on HOKUSAI BigWaterfall2 (HBW2), dependents of a failed `afterok`
job are **never** cancelled automatically. A chain with a broken segment
leaves the rest pending with `DependencyNeverSatisfied`. So either:
- submit the next segment after checking the previous one (safest); or
- chain at most one or two ahead with
  `"custom_attributes": {"dependency": "afterok:<id>", "kill-on-invalid-dep": "yes"}`
  (the second option is untested here). If a segment fails, cancel its
  dependents by explicit ID with the user's OK.

`WALLTIME` makes CP2K exit 0 after a clean stop, so `afterok` fires.

## 5. Check the run

`<PROJECT>-1.ener` has one line per step:
`Step  Time[fs]  Kin[a.u.]  Temp[K]  Pot[a.u.]  Cons Qty[a.u.]  UsedTime[s]`.

| check | healthy | if not |
|---|---|---|
| SCF | no `SCF run NOT converged` after the first step | **stop the run** (`cancel_job`): forces from an unconverged SCF are unreliable. Fix the SCF with single points first |
| `Temp` | after equilibration, the NVT mean approaches the target. Fluctuations of about T·√(2/(3N)) are physics (~12% for 64 atoms), not noise to fix. A sharp early drop (300 → ~200 K within ~10 fs) is kinetic energy flowing into the potential | still drifting: equilibrate longer |
| `Cons Qty` drift | < ~1 meV/atom/ps good; 1–10 acceptable for short runs; > 10 something is wrong. Only meaningful over ≳ 0.5 ps | shorter `TIMESTEP`, tighter `EPS_SCF` |
| `UsedTime` | steady | rising: more SCF iterations per step, so check the SCF |

Discard the equilibration part before averaging anything. Trajectories
grow with every step (atoms × steps × ~60 bytes per file); print every 5th
step (`&EACH MD 5`) for long runs. Home has a 10-million-file quota, so
keep few large files.
