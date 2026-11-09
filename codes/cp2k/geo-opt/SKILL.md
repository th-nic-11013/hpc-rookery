---
name: {{SLUG}}-cp2k-geo-opt
description: Run a CP2K geometry optimisation (atomic positions, GEO_OPT) or cell optimisation (positions plus lattice, CELL_OPT) on {{DISPLAY_NAME}} - write the input, submit, restart when it hits the wall-time limit, and check convergence. Use whenever the user wants to relax, optimise or minimise a structure with CP2K on {{DISPLAY_NAME}}, including relaxing X-ray hydrogens or a built MOF before AIMD.
---

# CP2K geometry and cell optimisation on {{DISPLAY_NAME}}

Every tool call uses `facility="{{SLUG}}"`. Show the user the key
settings and the job spec, state the cost, and wait for permission before
submitting.

## Contract

- **Reads:** `benchmarks` (layout and step time) and `software_registry`,
  through `launch_plan`.
- **Writes:** one `benchmarks` record per run (`cp2k_record_from_run`).
  It writes the optimised geometry
  (`-FINAL-1_<step>.xyz` / `.cif`) and the restart files.
- **Tools:** `cp2k_make_input` (the hyogo server, writes the input;
  `guess_from` chains the next run),
  `launch_plan` (layout, job spec, cost), `fs_upload`,
  `cp2k_check_input` (parse check),
  `submit_job`, `get_job_status`, `fs_tail` / `fs_view`, `fs_download`
  (output and final frame), `cp2k_parse_output` (the hyogo server, reads
  the output), `cp2k_record_from_run` (the timing record),
  `structure_check` (the yagura server).
- **Preconditions:** the prerequisites below, and the user's approval of
  the input and the cost.
- **Done when:** `GEOMETRY OPTIMIZATION COMPLETED`, and the final frame
  passes `structure_check`. If the run stops before that, restart it
  (section 4) or report why it can't continue.

**Prerequisites:**
- a CP2K build: its `env_script` (written `<env_script>` below) from
  `software_registry` through `{{SLUG}}-records`, the `default: true`
  record unless the user names one (`{{SLUG}}-cp2k-build` makes builds);
- `subsys.inc` (`{{SLUG}}-structure-making`, `cp2k/cp2k.md`);
- **a converged single point on the starting geometry**
  (`{{SLUG}}-cp2k-single-point`). It proves the SCF settings work for this
  system before you pay for dozens of steps. On {{DISPLAY_NAME}}, an
  unconverged SCF once cost 153.6 core-hours for no result; a single point
  would have shown the problem for about 13.

## 1. Choose the run type

| the user wants | `run_type` | notes |
|---|---|---|
| relax atoms, fixed cell (molecule, slab, defect, X-ray H) | `"geo_opt"` | `RUN_TYPE GEO_OPT`, writes `geo_opt.inp` |
| relax atoms **and** lattice (bulk, MOF, equation of state) | `"cell_opt"` | `RUN_TYPE CELL_OPT` with the analytical stress tensor, writes `cell_opt.inp`. Default `CUTOFF` 600: stress needs a higher cutoff than energies (Pulay stress), so converge it on the stress |

```text
cp2k_make_input(run_type="geo_opt", functional=<as the single point>,
                subsys=<cp2k_make_subsys' result>, kinds=<cp2k_kinds' result>,
                duration=<launch_plan's suggested_duration>, opt={"max_iter": 200})
```

It writes the single point's DFT block plus ASPC wavefunction
extrapolation between steps. Pass the same `functional`, `cutoff`, `scf`
and `multiplicity` as the single point, or the energies won't be
comparable. Read its `warnings` to the user.

Settings to confirm:
- **Optimiser:** `BFGS` up to a few hundred atoms. For larger systems use
  `LBFGS`, since BFGS keeps a dense Hessian. `CG` is a robust fallback
  when BFGS oscillates.
- **Convergence:** `MAX_FORCE 4.5E-4` hartree/bohr (≈ 0.023 eV/Å, CP2K's
  default). Tighten to `1.0E-4` before computing frequencies, or to match
  an earlier run: `opt={"max_force": 1e-4, "rms_force": 5e-5}`.
- **Fixed atoms:** the tool doesn't write them. Add the
  `&CONSTRAINT / &FIXED_ATOMS` block (commented in `templates/geo_opt.inp`)
  to the written input, with 1-based indices in `subsys.inc` order.
- **Cell optimisation:** `EXTERNAL_PRESSURE` 1 bar, `PRESSURE_TOLERANCE`
  100 bar. To keep the lattice (a cubic MOF staying cubic), call
  `cp2k_make_subsys(..., symmetry="CUBIC")` (it refuses a cell that isn't)
  and pass `opt={"keep_angles": true, "keep_symmetry": true}`. Ask the user
  first: it forbids a real symmetry-lowering distortion. Don't add a
  `TYPE` keyword: recent CP2K removed it (`--check` aborts on it), and
  direct cell optimisation is always used.
- **`WALLTIME`** (seconds) is the job's duration minus 10 minutes, from
  `duration`: `12:00:00` → `42600`. CP2K then stops cleanly and writes its
  restart file before Slurm kills the job. Without `duration` the tool
  assumes 12 hours and warns.

Parse-check on the login node before submitting:
`cp2k_check_input(facility="{{SLUG}}", input_path="<run dir>/geo_opt.inp")` (the default build;
pass `build_id` for another). `ok: false` comes with the `errors`;
read `warnings` too: a missing restart wavefunction (`files`) leaves `ok` true.
Both templates passed `--check` and ran to completion on CP2K 2026.2.

**By hand, without the hyogo server:** copy `templates/geo_opt.inp` or
`templates/cell_opt.inp` (revPBE-D3 only) and set its `@SET` lines.

## 2. Stage and submit

Stage the input and `subsys.inc` in `~/agent/work/cp2k-runs/<system>/opt/`.

Ask rookery for the launch, never write it by hand:

```text
launch_plan(facility="{{SLUG}}", code="cp2k", run_type="geo_opt",
            system={"label": "<system>", "atoms": <atoms>}, steps=200,
            build_id=<only if the user named a build>)
```

Submit its `job_spec` (`pre_launch`, `launcher`, `environment`,
`resources`, `attributes.queue_name`) unchanged, with these added:

```json
{
  "name": "cp2k-opt-<system>",
  "directory": "/home/<user>/agent/work/cp2k-runs/<system>/opt",
  "executable": "cp2k.psmp -i geo_opt.inp -o geo_opt.out",
  "attributes": {"queue_name": "<from job_spec>", "duration": "12:00:00"}
}
```

Use `run_type="cell_opt"` for a cell optimisation. If `basis.match` is
`"none"`, call again with `run_type="aimd_step"`: after the first step, each
optimisation step is an ASPC-extrapolated SCF like an MD step, so the MD
ranking carries over. Name the record (`basis.record_ids`) and say how good
the match is (`exact`, or `similar`: rough). Use more nodes only for large
cells; `basis.options` shows what they buy. The binding rules are in
`{{SLUG}}-cp2k-single-point`.

Cost: `estimate` takes one step after the first at the matched record's
`metric.value` (s per MD step) × `steps`; typical runs take 50–200 steps,
so ask with the upper end. Tell the user the worst case: nodes ×
{{CP2K_CORES_PER_NODE}} × duration in hours.

## 3. Check progress and the result

`fs_download` the `.out` and call `cp2k_parse_output` on it (it reads a
running job's output too). `opt` holds `converged`, `steps`,
`max_iter_hit`, each criterion with its value, limit and YES/NO
(`max_step`, `rms_step`, `max_gradient`, `rms_gradient`, and for
cell_opt `pressure_dev_bar`), and `internal_pressure_bar`; `cell` is the
last cell (a, b, c, angles, volume); `gap_ev` and `energy_ha` are the last
step's (judge the gap with yagura's `gap_compare`, as in
single-point). Its `warnings` say which case applies:

- `opt.converged`: done. Report the final cell and energy from the result.
- finished, not converged, `max_iter_hit` false: stopped at `WALLTIME`.
  Restart (section 4).
- `max_iter_hit`: not converged after `MAX_ITER`. Restart, or look at the
  setup if the energy is not going down.
- not `finished`: still running, or killed (`get_job_status`).

Record the timing (it is what `launch_plan` sizes the next run from): `cp2k_record_from_run` with the whole parse result as `parsed`, `nodes`, `job_id`, and `like` = a `benchmarks` id of the same system and method (`records_query`), overriding what differs with `method={...}` (e.g. `{"functional": "PBE0"}`). Use `dry_run: true` first to show the user the record. Don't type a record by hand ({{SLUG}}-records). A run stopped by the walltime is still a valid per-step time; record each segment.

By hand, if the hyogo server isn't available:

| in the `.out` | meaning |
|---|---|
| `OPTIMIZATION STEP:` | one per step |
| `OPT\| Total energy [hartree]`, `OPT\| Used time [s]` | energy and time of that step |
| `OPT\| Maximum gradient is converged  YES/NO` (and the same for maximum step size, RMS step size, RMS gradient) | progress on each criterion (2026.2 wording; `grep "is converged"`) |
| `GEOMETRY OPTIMIZATION COMPLETED` | **converged** |
| `MAXIMUM NUMBER OF OPTIMIZATION STEPS REACHED` | not converged. Restart (section 4) |
| ended at `WALLTIME` | not converged. Restart |

Output files (checked on CP2K 2026.2):

| file | holds |
|---|---|
| `<PROJECT>-pos-1.xyz` | the trajectory, one frame per step |
| `<PROJECT>-FINAL-1_<step>.xyz` / `.cif` | the final geometry, written at the end (as of 2026.2) |
| `<PROJECT>-1.restart` | a complete input at the latest geometry, for restarting |
| `<PROJECT>-1.cell` | lattice per step (CELL_OPT) |
| `<PROJECT>-BFGS.Hessian` | the optimiser's Hessian (BFGS) |

Energy rising for several steps in a row, or a gradient that oscillates,
usually means a noisy SCF: tighten `EPS_SCF` to `1.0E-7`, or switch to
`CG`. Before handing the optimised structure to AIMD, download the final
frame and run `structure_check` on it (`{{SLUG}}-structure-making`).

### Use the result in the next run

A follow-up (a single point or another functional on the relaxed cell, or
AIMD) takes the structure and the wavefunction from this run. Don't type
either file name:

1. `cp2k_parse_output` on this run's output gives `files`. These are names
   inside the run folder: `final_xyz` (as CP2K named it; extended xyz with
   the cell), `wfn` and `restart`.
2. `fs_download <run folder>/<files.final_xyz>`, then `structure_check`.
   Then `cp2k_kinds` and `cp2k_make_subsys` for the new run, in a new
   folder beside this one.
3. `cp2k_make_input(..., guess_from=<the parse result>,
   guess_dir="../<this run's folder>")`. It refuses a wavefunction from
   other atoms, electrons, basis or potential files: a different basis
   family needs a guess run instead (`purpose="guess"`, single-point
   skill). It warns if this run didn't finish or converge.
4. `fs_upload`, then `cp2k_check_input`: its `files` confirm the
   wavefunction exists on the cluster.

## 4. Restart

Inputs from `cp2k_make_input` start with `@SET RESTART 0`; the result's
`restart` names the files. To continue after a walltime stop, change it
to `@SET RESTART 1` in the run folder's input and resubmit with a new
output name (`-o geo_opt_2.out`). That turns on `&EXT_RESTART` from
`<PROJECT>-1.restart` and starts the SCF from the run's own
`<PROJECT>-RESTART.wfn`, so the optimisation continues from the latest
geometry and step counter. The BFGS Hessian is rebuilt.

A hand-written input needs the same by hand: `&EXT_RESTART` with
`RESTART_FILE_NAME <PROJECT>-1.restart` at the top level, and
`SCF_GUESS RESTART`.

Prefer one longer job to a dependency chain.

{{CODE_NOTES}}

If you do chain jobs, use
`"custom_attributes": {"dependency": "afterok:<id>", "kill-on-invalid-dep": "yes"}`
(the second option is untested here). Check for stuck dependents, and
cancel them by explicit ID with the user's OK.
