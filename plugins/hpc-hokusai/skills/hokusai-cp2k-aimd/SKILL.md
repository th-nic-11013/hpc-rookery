---
name: hokusai-cp2k-aimd
description: Run CP2K ab initio (Born-Oppenheimer) molecular dynamics on HOKUSAI BigWaterfall2 (HBW2) - NVT equilibration and NVE/NVT production, cost estimates from measured step times, restarts across the 24-hour limit, checks on temperature, energy conservation and SCF, and trajectory analysis (RDF, MSD, vibrational DOS). Use whenever the user wants AIMD, BOMD, first-principles MD, a trajectory, or to continue or restart an MD run with CP2K on HOKUSAI BigWaterfall2 (HBW2).
---

# CP2K AIMD on HOKUSAI BigWaterfall2 (HBW2)

Every tool call uses `facility="hokusai"`. Show the user the key settings
and the job spec, state the cost, and wait for permission before
submitting. AIMD is usually the most expensive thing done with CP2K, so
give the core-hour estimate before every submission.

## Contract

- **Reads:** `benchmarks` (layout and step time) and `software_registry`,
  through `launch_plan`.
- **Writes:** one `benchmarks` record per segment (`cp2k_record_from_run`).
  It writes the trajectory, `.ener` and restart files.
- **Tools:** `cp2k_make_input` (the hyogo server, writes the input),
  `launch_plan` (layout, job spec, cost), `fs_upload`,
  `cp2k_check_input` (parse check),
  `submit_job`, `get_job_status`, `cancel_job`, `fs_tail` / `fs_view`,
  `fs_download`, `cp2k_parse_output` (the hyogo server: finished, SCF per
  step, steps done, time per step), `cp2k_record_from_run` (the timing
  record); for analysis, `trajectory_rdf`, `trajectory_msd` and
  `trajectory_vdos` (yagura, on the downloaded files).
- **Preconditions:** the prerequisites below, and the user's approval of
  the core-hour estimate for every segment.
- **Done when:** each segment ended cleanly (SCF converged, conserved
  quantity within the limits in section 5) and the next segment is
  submitted or the run is complete. A failed segment is reported and its
  dependents are dealt with.

**Prerequisites:**
- a CP2K build: its `env_script` (written `<env_script>` below) from
  `software_registry` through `hokusai-records`, the `default: true`
  record unless the user names one (`hokusai-cp2k-build` makes builds);
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

**Estimate the cost from measured step times** with `launch_plan`
(section 3), passing `steps`. Its `estimate` gives `total_core_hours` and
`suggested_duration` from the matched record's s per steady step:

```text
core-h = s_per_step × steps × nodes × 112 / 3600
```

- `optimize="cost"` (default) takes the node count with the fewest
  core-hours; `optimize="time"` the fastest still at ≥ 70 % parallel
  efficiency. Show the user `basis.options` when they ask what more nodes
  would buy.
- `basis.match: "similar"` is an order of magnitude only; say so.
  `"none"` gives no estimate: benchmark first, or give a range and say why.
- Check `method.scf` in `basis.method`. Records timed with
  `FULL_SINGLE_INVERSE` run a little faster than the template's
  `FULL_ALL`, whose preconditioner builds took 16% of a 456-atom step.
- The first step costs 7–10 steady steps (SCF from the atomic guess).
- For a new system, run 20–50 steps first. Read `UsedTime` (the last
  column of `<PROJECT>-1.ener`) or `MD| CPU time per MD step`, skip the
  first few steps, and multiply.
- Tell the user the total core-hours and how many 24 h jobs it means.

## 2. Write the input

```text
cp2k_make_input(run_type="md_nvt", functional=<as the single point>,
                subsys=<cp2k_make_subsys' result>, kinds=<cp2k_kinds' result>,
                duration=<launch_plan's suggested_duration, at most 24:00:00>,
                md={"temperature": 300, "timestep": 0.5, "steps": 2000, "timecon": 100})
```

It writes `aimd_nvt.inp` beside `subsys.inc`, with the single point's DFT
block (pass the same `functional`, `cutoff`, `scf`, `multiplicity`) plus:
- ASPC extrapolation (order 3), which makes later steps several times cheaper;
- trajectory and velocities every step;
- a restart file every 10 steps;
- `WALLTIME` = `duration` minus 15 minutes (`24:00:00` → `85500`), so
  CP2K stops cleanly with a fresh restart file. Without `duration` the
  tool assumes 24 hours and warns.

Read its `warnings` to the user (e.g. a timestep above 0.5 fs with H).

`FULL_ALL` converged every cell tested on HOKUSAI BigWaterfall2 (HBW2).
`FULL_SINGLE_INVERSE` is slightly cheaper for ~100-atom cells, but it
stalled on a 456-atom cell.

**By hand, without the hyogo server:** copy `templates/aimd_nvt.inp`
(revPBE-D3 only) and set its `@SET` lines.

**NVE production:** set `ENSEMBLE NVE` in the written input and delete
`&THERMOSTAT`. Start it
from the equilibrated run's restart (section 4) so it inherits the
velocities.

Parse-check on the login node:
`cp2k_check_input(facility="hokusai", input_path="<run dir>/aimd_nvt.inp")` (the default build;
pass `build_id` for another). `ok: false` comes with the `errors`;
read `warnings` too: a missing restart wavefunction (`files`) leaves `ok` true.

## 3. Stage and submit

Stage the input and `subsys.inc` in `~/agent/work/cp2k-runs/<system>/md/`.

Ask rookery for the launch, never write it by hand:

```text
launch_plan(facility="hokusai", code="cp2k", run_type="aimd_step",
            system={"label": "<system>", "atoms": <atoms>}, steps=<STEPS>,
            build_id=<only if the user named a build>)
```

Submit its `job_spec` (`pre_launch`, `launcher`, `environment`,
`resources`, `attributes.queue_name`) unchanged, with these added:

```json
{
  "name": "cp2k-md-<system>",
  "directory": "/home/<user>/agent/work/cp2k-runs/<system>/md",
  "executable": "cp2k.psmp -i aimd_nvt.inp -o aimd_nvt.out",
  "attributes": {"queue_name": "<from job_spec>", "duration": "<estimate.suggested_duration>"}
}
```

Use the same call as for the cost estimate, so the layout and the
estimate agree. Name the record (`basis.record_ids`); on `"similar"` or
`"none"` suggest `hokusai-cp2k-benchmark` before a long run. The binding
rules are in `hokusai-cp2k-single-point`.

### On HOKUSAI BigWaterfall2 (HBW2)

- **Wall time:** `mpc` allows 24 h. `mpc_l` allows 72 h, but only 8 nodes,
  and long jobs find backfill gaps less easily. Prefer 24 h segments on
  `mpc`.
- **Dependencies:** dependents of a failed `afterok` job are **never**
  cancelled automatically.
- **Files:** home has a 10-million-file quota.

## 4. Continue a run

Inputs from `cp2k_make_input` start with `@SET RESTART 0`. A
continuation is a copy of the input with `@SET RESTART 1`: that turns on
`&EXT_RESTART` from `<PROJECT>-1.restart` and `SCF_GUESS RESTART` from the
run's own wavefunction. (A hand-written input needs `SCF_GUESS RESTART`
in `&SCF` and, at the top level, `&EXT_RESTART` with
`RESTART_FILE_NAME <PROJECT>-1.restart`.)

This restores positions, velocities, thermostat state and the step
counter. Verified on CP2K 2026.2:
- **`STEPS` then counts additional steps.** A continuation from step 4
  with `STEPS 3` ran steps 5–7. Set `STEPS` to the number of steps this
  segment should run.
- **`<PROJECT>-1.ener`, `-pos-1.xyz` and `-vel-1.xyz` are appended to**,
  so the run stays one set of files.

Write each segment's log to a new file (`-o aimd_nvt_2.out`). Changing
`TIMECON` between equilibration and production (100 → 200 fs) is normal.

Chaining segments: if the scheduler does not cancel the dependents of a
failed `afterok` job (see "On HOKUSAI BigWaterfall2 (HBW2)" above), a chain with a broken
segment leaves the rest pending with `DependencyNeverSatisfied`. So either:
- submit the next segment after checking the previous one (safest); or
- chain at most one or two ahead with
  `"custom_attributes": {"dependency": "afterok:<id>", "kill-on-invalid-dep": "yes"}`
  (the second option is untested here). If a segment fails, cancel its
  dependents by explicit ID with the user's OK.

`WALLTIME` makes CP2K exit 0 after a clean stop, so `afterok` fires.

## 5. Check the run

`fs_download` the `.out` and call `cp2k_parse_output`: `finished`,
`scf.not_converged` (any after the first step: stop the run, below),
`md.steps` done and `md.s_per_step`, and `warnings`. A finished segment
with fewer steps than asked stopped at `WALLTIME`: continue it (section 4).

Record the timing (it is what `launch_plan` sizes the next run from): `cp2k_record_from_run` with the whole parse result as `parsed`, `nodes`, `job_id`, and `like` = a `benchmarks` id of the same system and method (`records_query`), overriding what differs with `method={...}` (e.g. `{"functional": "PBE0"}`). Use `dry_run: true` first to show the user the record. Don't type a record by hand (hokusai-records).

`<PROJECT>-1.ener` has one line per step:
`Step  Time[fs]  Kin[a.u.]  Temp[K]  Pot[a.u.]  Cons Qty[a.u.]  UsedTime[s]`.

| check | healthy | if not |
|---|---|---|
| SCF | no `SCF run NOT converged` after the first step | **stop the run** (`cancel_job`): forces from an unconverged SCF are unreliable. Fix the SCF with single points first |
| `Temp` | after equilibration, the NVT mean approaches the target. Fluctuations of about T·√(2/(3N)) are physics (~12% for 64 atoms), not noise to fix. A sharp early drop (300 → ~200 K within ~10 fs) is kinetic energy flowing into the potential. With `TIMECON` 100 fs, a 114-atom MOF started at 300 K was still at 282 K after 500 fs | still drifting: equilibrate longer |
| `Cons Qty` drift | < ~1 meV/atom/ps good; 1–10 acceptable for short runs; > 10 something is wrong. Only meaningful over ≳ 0.5 ps | shorter `TIMESTEP`, tighter `EPS_SCF` |
| `UsedTime` | steady | rising: more SCF iterations per step, so check the SCF |

Discard the equilibration part before averaging anything. Trajectories
grow with every step (atoms × steps × ~60 bytes per file); print every 5th
step (`&EACH MD 5`) for long runs, and keep few large files rather than
many small ones.

## 6. Analyse the trajectory

`fs_download` `<PROJECT>-pos-1.xyz` (and `-1.ener`) from the run directory,
then use the yagura tools on the local copy:

- `trajectory_rdf(path, pairs=["Zr-O", "O-H"], cell=..., frames="<first production frame>:")`:
  first-shell distances and coordination numbers, a quick check that the
  bonding survived (e.g. the expected metal coordination, no lost O-H).
- `trajectory_msd(path, elements=[...], dt_fs=..., cell=..., frames=...)`:
  diffusion. Report `D` only when `loglog_slope` is near 1 **and** the MSD
  reaches several Å². In a framework, sub-ps runs give slopes near 1 from
  vibration alone (0.25 ps of UiO-66 H: slope 1.09, MSD 0.27 Å²): that is
  not diffusion. Framework atoms plateau; MSD is for mobile guests.
- `trajectory_vdos(path, dt_fs=..., cell=..., elements=[...])`: peak
  positions (X-H stretches, carboxylate modes). Resolution is 1 / run
  length (~67 cm⁻¹ for 0.5 ps), so long runs resolve close peaks.

Arguments:
- **`cell`**: CP2K's `-pos-1.xyz` has no cell. Pass the cell vectors as
  a 3×3 matrix of rows, the same as `&CELL` `A`/`B`/`C` (or an extxyz
  `Lattice=` line split into three rows).
- **`dt_fs`**: `TIMESTEP` × the trajectory's `&EACH MD` stride. For VDOS keep
  the Nyquist limit (16678 / dt_fs cm⁻¹) above the X-H stretches: 0.5 fs
  with `&EACH MD 10` gives 3336 cm⁻¹, which already cuts them off.
- **`frames`**: drop the equilibration part (`"500:"`), as for every
  average.
