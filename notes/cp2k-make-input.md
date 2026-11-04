# Plan: `cp2k_make_input` (hyogo), gap G3

2026-11-04. Written with the `hyogo-intake` skill. Status: **built, not committed**.

Built as planned, with these changes:
- the default output names follow the skills' templates
  (`single_point.inp`, `geo_opt.inp`, `cell_opt.inp`, `aimd_nvt.inp`), or
  `<project>.inp` when `project` is given;
- `cp2k_kinds` families are named `molopt` (default, unchanged),
  `uzh-pbe`, `uzh-scan` and `uzh-pbe0`; UZH reads the newest versioned
  file, and the basis name carries its `-qN` (`DZVP-MOLOPT-SCAN-GTH-q12`);
- `cp2k_make_subsys` also returns `charge` and `aux_basis`;
- `save_wfn` was added (the UiO-66 inputs save the SCF wavefunction).

Verified:
- conformance: the 4 templates and the 5 UiO-66 inputs match after
  normalising. The only difference is that the GGA UiO-66 inputs listed
  `BASIS_ADMM_UZH` though they don't use it;
- the UiO-66 `AUX_FIT` subsys files match byte for byte;
- all 24 combinations (6 presets × 4 run types) on UiO-66 pass the local
  `cp2k --check` (2026.2), with real kinds from the local data folder.

Not verified: any SCF actually running, so the HFX and ADMM runtime path
is still untested, as in uio66-functionals.md §5. Also not yet done:
`cp2k_kinds(family=...)` live on HOKUSAI.

## Sources

- `codes/cp2k/single-point/templates/single_point.inp`,
  `codes/cp2k/geo-opt/templates/{geo_opt,cell_opt}.inp`,
  `codes/cp2k/aimd/templates/aimd_nvt.inp`: revPBE-D3 only. They share one
  DFT block, which is kept "in step" by hand across the four files.
- The UiO-66 functionals generator, `make_inputs.sh`, with its five
  outputs. They live in another session's scratchpad
  (`/private/tmp/claude-501/-Users-nic-th-Downloads-research-hpc-rookery/b0c34a19-0cb9-416c-877c-d805fee8de42/scratchpad/uio66-func/`),
  and `/tmp` does not last, so they get copied into fixtures. This is the
  only working source for PBE, r²SCAN, PBE0, HSE06 and r²SCAN0, each with
  D3(BJ) and ADMM. `cp2k --check` passed on all five.
- Gaps G1, G2 and G3 in [uio66-functionals.md](uio66-functionals.md).

## The split

| unit | what it is | owner |
|---|---|---|
| choosing the functional, cutoff, and whether to run radius/cutoff scans | judgment | prose (single-point skill, a new "functionals" section) |
| basis and potential family per functional, plus the file names (`BASIS_MOLOPT_UZH_2026.2`, ...) | fact of the build's data folder | **rookery**: G1, a `family` argument on `cp2k_kinds` |
| `BASIS_SET AUX_FIT` per kind | input text (&SUBSYS) | **hyogo**: G2, extends live `cp2k_make_subsys` |
| shared DFT skeleton: &GLOBAL, &MGRID, &QS, &SCF (OT or smearing), &POISSON when PERIODIC NONE, UKS/multiplicity | input text | **hyogo**: new `cp2k_make_input` |
| &MOTION per run type: GEO_OPT, CELL_OPT plus stress, MD NVT/CSVR plus restart prints | input text | **hyogo**: same tool |
| XC presets: functional sections, &HF (truncated or short-range), D3 key and damping, &AUXILIARY_DENSITY_MATRIX_METHOD | input text | **hyogo**: same tool |
| HFX radius below half the minimum cell width | method check | **hyogo**: folded into `cp2k_make_input`. It needs `widths` from `cp2k_make_subsys`, and it only matters while writing the input, so the `cp2k_check_hfx` candidate goes |
| WALLTIME from the job's duration | fact of the launch | an argument: `walltime` from `launch_plan`'s job_spec |
| parse check on the build | build fact | rookery `cp2k_check_input`, existing; its G4/G5 are separate |
| restarting MD across the 24-hour limit | `md` / `restart` | later, its own tool (`cp2k_restart_md`, candidate) |

## Category

`cp2k_make_input`: method `any` (it writes sp, opt and md inputs), stage
`input`, code `cp2k`, module `hyogo/cp2k/input.py`. No new category is
needed.

## Signature

```
cp2k_make_input(run_type, functional, subsys, kinds, out_path,
                cutoff=None, rel_cutoff=60, d3=None, scf="ot",
                walltime=None, md=None, opt=None, hfx=None,
                print_gap=False, overwrite=False)
  -> {"out", "run_type", "functional", "settings": {...}, "files": [...],
      "warnings": [...]}
```

- `run_type`: `"sp"` | `"geo_opt"` | `"cell_opt"` | `"md_nvt"`.
- `functional`: a preset, one of `revpbe`, `pbe`, `r2scan`, `pbe0`,
  `hse06`, `r2scan0`. Each preset carries its own XC text, HF fraction and
  interaction type, D3 key, default damping (`revpbe` zero, the benchmarked
  baseline; every other preset BJ) and default cutoff (450 Ry for GGA, 600
  for r²SCAN and the hybrids).
- `subsys`: `cp2k_make_subsys`' result. The tool takes `periodic` (for
  &POISSON), `electrons` (odd means UKS and multiplicity 2) and `widths`
  (the HFX radius) from it.
- `kinds`: `cp2k_kinds`' result. The tool takes the basis and potential
  file names from it (after G1) and checks that the potential family
  matches the preset. For example, refuse `GTH-PBE-q*` with `r2scan`, with
  the message "call cp2k_kinds(family='scan')". Hybrids refuse kinds that
  have no aux basis.
- `hfx`: `{radius, memory_mb}`. `radius` defaults to the largest 0.1 Å
  step strictly below min(widths)/2; for UiO-66, 11.97 Å gives 5.9.
  Anything larger is refused.
- `d3="zero"` with an r²SCAN preset is refused, because the parameters
  exist only in BJ form.
- One file out. XC is written inline, with no `xc.inc`, and tunables
  (`CUTOFF`, `HFX_RADIUS`, `STEPS`, ...) stay as `@SET` lines at the top
  so a person can edit them.
- Warnings, for the agent to read to the user:
  - r²SCAN0 has no long-range correction beyond the radius;
  - hybrid cost is roughly 5–20× the GGA cost, so run a short test job
    first;
  - `t_c_g.dat` must be in the build's data folder;
  - metals and small-gap systems need `scf="smear"`.

## Agent flow

`structure_check` → (`structure_stretch_xh`) → `cp2k_kinds(family=…)` →
`cp2k_make_subsys` → **`cp2k_make_input`** → `fs_upload` →
`cp2k_check_input` → `launch_plan` → `submit_job`.

## Tests

- Golden files: copy the five UiO-66 outputs (`sp.inp` with `xc.inc`
  inlined) to `tests/fixtures/cp2k_inputs/uio66/`, and use the four
  templates as they are.
- Compare after normalising: drop comments and blank lines, and substitute
  the `@SET` values. A byte-for-byte comparison would be wrong here,
  because the templates are full of commented-out alternatives. Coverage:
  - the four run types with `revpbe` against the templates;
  - `sp` with the five other presets against UiO-66, with HOMO/LUMO
    printing on (`print_gap=True`), since those outputs print it.
- Refusals:
  - kinds from the wrong family;
  - a hybrid without an aux basis;
  - an HFX radius at or above half the width;
  - zero damping with r²SCAN;
  - an unknown preset or run type;
  - an existing `out_path`.
- PERIODIC NONE gives &POISSON, and an odd electron count gives UKS.
- The server probe needs `cp2k_make_input` set to `live` in INVENTORY.md.

## Order and commits

1. rookery: G1, `cp2k_kinds(family, aux_basis)` returning the file names
   (`hpc_codes/`).
2. hyogo: G2, `aux_basis` in `cp2k_make_subsys`. Then `cp2k_make_input`,
   with its fixtures and conformance check.
3. codes: the single-point, geo-opt and AIMD skills call the tool. The
   templates stay as the by-hand fallback, and a "functionals" section
   goes into `single-point/SKILL.md`. Then re-render the skills and
   plugins.

Steps 1 and 2 are independent apart from the file names: until G1 lands,
`cp2k_make_input` falls back to `BASIS_MOLOPT` / `GTH_POTENTIALS` for the
GGA presets and refuses the others.

## Open

- The non-revPBE presets for geo_opt, cell_opt and md_nvt pass the parse
  check only; there is no golden file to compare them against.
