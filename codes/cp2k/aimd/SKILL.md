---
name: {{SLUG}}-cp2k-aimd
description: Run CP2K ab initio (Born-Oppenheimer) molecular dynamics on {{DISPLAY_NAME}} - NVT equilibration and NVE/NVT production, cost estimates from measured step times, restarts across the 24-hour limit, and checks on temperature, energy conservation and SCF. Use whenever the user wants AIMD, BOMD, first-principles MD, a trajectory, or to continue or restart an MD run with CP2K on {{DISPLAY_NAME}}.
---

# CP2K AIMD on {{DISPLAY_NAME}}

Every tool call uses `facility="{{SLUG}}"`. Show the user the key settings
and the job spec, state the cost, and wait for permission before
submitting. AIMD is usually the most expensive thing done with CP2K, so
give the core-hour estimate before every submission.

## Contract

- **Reads:** `benchmarks` (layout and step time) and `software_registry`,
  through `launch_plan`.
- **Writes:** no records. It writes the trajectory, `.ener` and restart
  files.
- **Tools:** `launch_plan` (layout, job spec, cost), `fs_upload`,
  `cp2k_check_input` (parse check),
  `submit_job`, `get_job_status`, `cancel_job`, `fs_tail` / `fs_view`.
- **Preconditions:** the prerequisites below, and the user's approval of
  the core-hour estimate for every segment.
- **Done when:** each segment ended cleanly (SCF converged, conserved
  quantity within the limits in section 5) and the next segment is
  submitted or the run is complete. A failed segment is reported and its
  dependents are dealt with.

**Prerequisites:**
- a CP2K build: its `env_script` (written `<env_script>` below) from
  `software_registry` through `{{SLUG}}-records`, the `default: true`
  record unless the user names one (`{{SLUG}}-cp2k-build` makes builds);
- `subsys.inc` (`{{SLUG}}-structure-making`), ideally relaxed first
  (`{{SLUG}}-cp2k-geo-opt`). X-ray C-H bonds (~0.95 Å) put large forces on
  the first steps;
- a converged single point on the structure (`{{SLUG}}-cp2k-single-point`);
- for anything long, a layout from `{{SLUG}}-cp2k-benchmark` measured on
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
core-h = s_per_step × steps × nodes × {{CP2K_CORES_PER_NODE}} / 3600
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

Start from `templates/aimd_nvt.inp`. It has the single-point DFT block
(revPBE-D3, DZVP-MOLOPT-SR-GTH, 450/60 Ry, OT DIIS + FULL_ALL) plus:
- ASPC extrapolation (order 3), which makes later steps several times cheaper;
- trajectory and velocities every step;
- a restart file every 10 steps;
- `WALLTIME` = the job's duration minus ~15 minutes (`24:00:00` → `85500`),
  so CP2K stops cleanly with a fresh restart file.

`FULL_ALL` converged every cell tested on {{DISPLAY_NAME}}.
`FULL_SINGLE_INVERSE` is slightly cheaper for ~100-atom cells, but it
stalled on a 456-atom cell.

**NVE production:** set `ENSEMBLE NVE` and delete `&THERMOSTAT`. Start it
from the equilibrated run's restart (section 4) so it inherits the
velocities.

Parse-check on the login node:
`cp2k_check_input(facility="{{SLUG}}", input_path="<run dir>/aimd_nvt.inp")` (the default build;
pass `build_id` for another). `ok: false` comes with the `errors`.

## 3. Stage and submit

Stage the input and `subsys.inc` in `~/agent/work/cp2k-runs/<system>/md/`.

Ask rookery for the launch, never write it by hand:

```text
launch_plan(facility="{{SLUG}}", code="cp2k", run_type="aimd_step",
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
`"none"` suggest `{{SLUG}}-cp2k-benchmark` before a long run. The binding
rules are in `{{SLUG}}-cp2k-single-point`.

### On {{DISPLAY_NAME}}

{{CODE_NOTES}}

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

Chaining segments: if the scheduler does not cancel the dependents of a
failed `afterok` job (see "On {{DISPLAY_NAME}}" above), a chain with a broken
segment leaves the rest pending with `DependencyNeverSatisfied`. So either:
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
step (`&EACH MD 5`) for long runs, and keep few large files rather than
many small ones.
