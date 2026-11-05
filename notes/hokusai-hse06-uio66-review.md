# Review: `~/software/cp2k/hse06_uio66` on HOKUSAI

2026-11-05. Read-only review (`fs_ls`, `fs_view`, `fs_tail`); nothing on
HOKUSAI was changed. Runs date from 2026-10-28 to 2026-10-31.

## What is there

```
hse06_uio66/
├── 00_prep/          structure (456-atom conventional cell), cell/coord includes,
│                     6 &KIND variants, PBE-D3(BJ) DFT block, check_kinds.sh
├── 01_pbe_cutoff/    Stage 1a: 12 runs c300…c1200 × r40…r80, template.inp, job.sh,
│                     2 job logs at the top
├── 02_pbe_opt/       Stage 1b: PBE-D3(BJ) CELL_OPT at 1000/60 Ry, done; params.inc
│                     (chosen grid), opt.xyz/opt_*.inc (extracted result)
├── 03_hse/           Stage 2: HSE06 single points on the PBE geometry
│   ├── defaults.inc, hse06_template.inp, run_one.sh, job_array.sh, tasks.txt
│   ├── T0_baseline/, T0_run1_9709932/ (superseded first run), stress_check/
│   └── T1_schwarz/ T2_admm/ T3_basis/ T4_eps/ T5_scf/ T6_radius/  (+ 11 logs at top)
├── 04_final/         empty
└── analysis/         collect_pbe_cutoff.py, collect_hse.py, extract_opt.py, 2 CSVs
```

## What it shows

| stage | result |
|---|---|
| 1a cutoff | 1000 Ry the lowest within 0.1 meV/atom, 1e-4 Ha/bohr, 0.1 GPa of 1200; REL_CUTOFF converged from 40 |
| 1b CELL_OPT | converged in 46 steps; a = 20.889 Å (exp. 20.734, +0.75 %) |
| 2 T0 baseline | HSE06 gap 4.0238 eV; ERIs 19.8 GiB in RAM |
| T1 EPS_SCHWARZ | 1e-5 → 1e-8: gap moves < 0.0004 eV; 1e-7/1e-8 need 89 GiB ERIs and recompute part on the fly (1e-8: 1675 s) |
| T2 ADMM | admm-dz −0.003 eV, **admm-tzp +0.054 eV** vs admm-dzp: above your own 0.05 eV tolerance |
| T3 basis | **both failed**: dzvp-uzh timed out (SCF from the atomic guess, 114 s per step, still at 1e-5 after 44 steps); tzvp-uzh segfaulted at 399 s |
| T4 EPS_DEFAULT, T5 EPS_SCF, T6 radius 8 Å | gap unchanged to 1e-4 eV |

The energy, force and stress numbers are not in this table: the summary CSV
is stale (next section), so only the gaps from the job logs are listed.

## Problems, most important first

1. **An 18.6 GB core dump** in `03_hse/T3_basis/tzvp-uzh/core-cp2k.psmp.595760`
   from the segfault. It is most of the folder's size and counts against
   the quota.
2. **`analysis/hse_tests.csv` is stale.** It was written on 10-30 at 12:53,
   before the test array ran (10-31). Every row is the T0 baseline compared
   with itself, so a reader would think every test is identical to T0.
   Re-run `collect_hse.py`.
3. **T3 (the basis test) has no result**, so the basis is the one setting
   that is not converged. And ADMM tzp shifts the gap by 0.054 eV.
   - dzvp-uzh: the PBE wavefunction can't be reused across a basis change.
     Run a cheap PBE single point in the UZH basis first and restart HSE06
     from it.
   - tzvp-uzh: read the top of `srun.err` for the cause before rerunning;
     a TZVP basis plus ADMM may simply need more memory or nodes.
4. **Runs live inside the build tree** (`~/software/cp2k/`, next to
   `cp2k-2026.2/` and `cp2k_env.sh`), so cleaning up runs risks touching
   the build.
5. **Absolute `/lustre/home/thnic123/...` paths in about 10 files**
   (`defaults.inc`, `params.inc`, every `job*.sh` and `run_one.sh`, the
   collectors). Moving or copying the project means editing all of them.
6. **No README.** The decisions (1000 Ry, the 10 Å radius, why 2 nodes,
   memory per rank) are spread over comments in `params.inc`,
   `defaults.inc` and `job_array.sh`. They're good comments, but there is
   no single place that gives the status.
7. **Logs at the wrong level.** Job logs sit at the top of `01_pbe_cutoff/`
   and `03_hse/`, while each run's `cp2k.out` sits in its own folder. Finding
   a run's log means matching an array index against `tasks.txt`.
8. **Bulk you won't read again:**
   - a 41 MB `.wfn` in about 20 run folders (around 0.8 GB). Only
     `01/c600_r60` and `02_pbe_opt` are restart sources;
   - 46 numbered `pbe-1_*.restart` files, the 15 MB BFGS Hessian, and
     `.bak` copies in `02_pbe_opt`;
   - `T0_run1_9709932/`, which is superseded by `T0_baseline`.
9. **`04_final/` is empty**, with nothing saying what it is for.

## Suggested layout

```
~/agent/work/cp2k-runs/UiO-66-conv/hse06/     (out of ~/software)
├── README.md            goal, status per stage, decisions + why, open items
├── project.inc          @SET ROOT …; every other file includes it (one path to edit)
├── 00_prep/
├── 01_pbe_cutoff/<case>/{run.inp,cp2k.out,job.log}
├── 02_pbe_opt/          keep: final restart, FINAL xyz/cif, opt_*.inc, the wfn
├── 03_hse_tests/<test>/<case>/{run.inp,cp2k.out,job.log}
├── 04_hse_production/   what 04_final was for, named
├── analysis/            collectors + CSVs, re-run after every stage
└── archive/             T0_run1, superseded inputs (or delete)
```

Cleanup, in order of how much space it frees, all after your OK:
1. the core dump;
2. the `.wfn` files that are not restart sources;
3. the numbered restarts and the Hessian in `02_pbe_opt`.

## What this means for the hyogo and rookery tools

Your working HSE06 setup is a combination the new tools refuse:

| | your validated setup | cp2k_kinds / cp2k_make_input today | gap |
|---|---|---|---|
| basis + potential | DZVP-MOLOPT-SR-GTH + GTH-PBE | `hse06` demands GTH-PBE0 (`uzh-pbe0`) | **G8** |
| ADMM aux | admm-dzp with the molopt family | `aux_basis` refused for `molopt` | **G9** |
| HFX range | `CUTOFF_RADIUS 10 Å` on the short-range operator (below L/2) | no radius for `hse06` | **G10** |
| stress in a single point | `STRESS 1` (analytical) | not offered for `sp` | G11 |
| precision | EPS_PGF_ORB 1e-20, MAX_MEMORY 3000 MiB | not settable (2000 MiB default) | G11 |
| cutoff | 1000 Ry (converged) | 600 default | fine: it's an argument |

Your `defaults.inc` gives the reason for G10: without a cutoff radius,
the erfc range at EPS_SCHWARZ 1e-6 is 14.3 Å, which is above half the
cell (10.44 Å).

Also worth adding: these timings (T0 at 2 nodes, the ERI memory per
EPS_SCHWARZ) as hybrid `benchmarks` records, so that `launch_plan` can
size the next HSE06 job.

## Proposed fixes (G8–G11)

The goal: `cp2k_make_input` reproduces your T0 baseline. That becomes the
test, a golden file made from `03_hse/T0_baseline/run.inp` with
`defaults.inc`, `params.inc` and `hse06_template.inp` expanded.

| gap | owner | fix |
|---|---|---|
| G8 | hyogo `cp2k_make_input` | Presets take a list of allowed potential families instead of one. `pbe0`/`hse06` accept GTH-PBE0 (preferred) **or GTH-PBE**; `r2scan`/`r2scan0` accept GTH-SCAN (preferred) or GTH-PBE. A non-preferred family gives a warning ("pseudopotential not matched to the functional"), not a refusal. Anything else is still refused. |
| G9 | rookery `cp2k_kinds` | Allow `aux_basis` with `family="molopt"`, looked up in BASIS_ADMM_UZH by the same `-qN`. Drop the refusal. |
| G10 | hyogo | `hse06` takes `hfx={"radius": ...}` on the short-range operator (CUTOFF_RADIUS inside SHORTRANGE). Default: the range where erfc(0.11 r)/r falls to EPS_SCHWARZ. If that exceeds half the narrowest width, cap it just below and warn (your 14.3 Å → 10.4 Å). Refuse a radius ≥ L/2, as for the truncated presets. |
| G11 | hyogo | `hfx` also takes `eps_schwarz` (1e-6) and `screen_on_initial_p`, with a default of 3000 MiB for `memory_mb`. `sp` gets `stress=True` (analytical stress tensor plus the print). A `precision={"eps_default", "eps_pgf_orb"}` argument. `guess_wfn=<path>` gives SCF_GUESS RESTART from a PBE wavefunction: the T3 lesson. |

Order: G9 (rookery, small) → G8, G10, G11 (hyogo) → golden test against
your T0 → the `--check` sweep → steps 1–2 on HOKUSAI with your geometry
(`02_pbe_opt/opt.xyz`).

Before building: read your `hse06_template.inp` (3.7 KB) to catch any
keyword not listed here.

## Functional-rule conflicts (2026-11-05)

You decided: where a run breaks a functional rule, correct the run rather
than loosen the tools. So **G8 and G9 are dropped** (the tools stay strict).
G10 and G11 stay: they are correct practice. G12 is new: it is the
generator's own conflict. Each source is cited after a check (web search
2026-11-05); "verify" marks one I could only partly confirm.

| # | where | conflict | source | verdict |
|---|---|---|---|---|
| C1 | your T0, all tests | HSE06 on GTH-PBE pseudopotentials with DZVP-MOLOPT-SR-GTH, a basis optimised with PBE. CP2K ships hybrid-matched pairs (UZH `GTH-PBE0`, `DZVP-MOLOPT-PBE0-GTH`) | CP2K manual, Pseudopotentials page: match the PP to the functional family, prefer a matching UZH pair for new production runs. Hutter (cp2k-user, 2020): use the PBE0 GTH set for hybrids. UZH protocol preprint (Mirhosseini et al., 2026) | **fix the run**: use `uzh-pbe0` kinds |
| C2 | your T2 | `admm-tzp` used as the "tighter" reference against a DZVP primary. ADMM assumes the auxiliary basis is *smaller* than the primary, so the +0.054 eV is not evidence that admm-dzp is unconverged. The proper reference is no ADMM (`job_noadmm.sh` exists but never ran) | Guidon, Hutter, VandeVondele, JCTC 6, 2348 (2010); CP2K manual "HFX with ADMM" (on auxiliary basis size) | **fix the test**: reference = no-ADMM run |
| C3 | your T3 | it compares the old SR basis with GTH_POTENTIALS (T0) against a UZH basis with POTENTIAL_UZH, so basis and PP change at once, and both are PBE-family under HSE06 | same as C1 | **redesign**: vary the basis within one hybrid-matched family (UZH PBE0 DZVP vs TZVP) |
| C4 | **the hyogo tool** (`hse06`, `pbe0`, `r2scan0`) and the UiO-66 generator | `EXCH_CORRECTION_FUNC PBEX` for HSE06. The manual says the correction should match the exchange part of the XC functional; for HSE06 that's short-range ωPBE. Your `DEFAULT` picks the matching one; PBEX (full-range) does not. Also: `ADMM_TYPE` sets a consistent method/purification/scaling combination | CP2K manual, AUXILIARY_DENSITY_MATRIX_METHOD (2026.2) | **fix the tool (G12)**; *verify* what DEFAULT resolves to for SHORTRANGE in 2026.2 |
| C5 | your T0 | HSE06 single points on the PBE-D3(BJ) geometry (HSE06//PBE-D3) | standard practice; your collector checks HSE06 forces < 1e-3 Ha/bohr | **fine** if that check passes |
| — | your T0 | capping the SHORTRANGE operator below half the cell (10 Å) | Guidon, Hutter, VandeVondele, JCTC 5, 3010 (2009) (Γ-point periodic HFX); CP2K manual, INTERACTION_POTENTIAL. *Verify* the exact L/2 statement in the paper | **correct**: G10 stays |
| — | your T0 | D3(BJ) with `REFERENCE_FUNCTIONAL HSE06` | Grimme, Ehrlich, Goerigk, JCC 32, 1456 (2011) (BJ damping). *Verify* that HSE06 has an entry in CP2K's `dftd3.dat` (the run would abort if it didn't) | **fine** |

Links:
- CP2K pseudopotentials: https://manual.cp2k.org/cp2k-2026_2-branch/methods/dft/pseudopotentials.html
- CP2K ADMM keywords: https://manual.cp2k.org/cp2k-2026_2-branch/CP2K_INPUT/FORCE_EVAL/DFT/AUXILIARY_DENSITY_MATRIX_METHOD.html
- CP2K HFX with ADMM: https://manual.cp2k.org/trunk/methods/dft/hartree-fock/admm.html
- CP2K INTERACTION_POTENTIAL: https://manual.cp2k.org/trunk/CP2K_INPUT/FORCE_EVAL/DFT/XC/HF/INTERACTION_POTENTIAL.html
- Hutter on PBE0 PPs: https://lists.cp2k.org/archives/cp2k-user/2020-September/014008.html
- UZH protocol: https://arxiv.org/abs/2606.11064
- Guidon 2010 (ADMM): https://www.osti.gov/biblio/1564680
- Guidon 2009 (periodic HFX): https://doi.org/10.1021/ct900494g

## Status of the tool fixes (2026-11-05, not committed)

Built in `hyogo/cp2k/input.py`:
- **G12**, checked in the CP2K 2026.2 source:
  - `EXCH_CORRECTION_FUNC DEFAULT`. `hfx_admm_utils.F` builds short-range
    XWPBE for SHORTRANGE and the PBE_HOLE_T_C_LR hole for TRUNCATED, while
    PBEX is full-range;
  - `MIN_PAIR_LIST_RADIUS -1` for hybrids, after the
    `hfx_energy_potential.F` warning;
  - `EPS_SCHWARZ_FORCES` = `EPS_SCHWARZ` (the keyword's own description).
- **G10**: HSE06's `CUTOFF_RADIUS` is CP2K's own erfc range (12.5, 14.3,
  15.9 and 17.4 Å for 1e-5 to 1e-8, matching your `defaults.inc`), capped
  below L/2 with a warning. A radius ≥ L/2 is refused.
- **G11**: `hfx` takes `eps_schwarz`, `memory_mb` (default 3000) and
  `screen_on_initial_p` (on only with a restart). Also `stress`,
  `precision.eps_pgf_orb`, `guess_wfn`, and `&HF_INFO`.
- G8 and G9 were dropped, so the tools stay strict.

Tests: the three UiO-66 hybrid reference files were corrected in the same
way (their README lists the edits). The conformance tests pass, and 26
inputs pass the local `cp2k --check`.

Not run: any SCF, so the corrected inputs are not yet validated against
a reference.
