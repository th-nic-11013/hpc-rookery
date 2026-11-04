# UiO-66 functional comparison: r²SCAN, r²SCAN hybrid, PBE0, HSE06 (all + D3)

2026-11-04. Discussion only — nothing submitted. Baseline: the revPBE-D3 AIMD
in [uio66-e2e.md](uio66-e2e.md) (114-atom primitive cell, DZVP-MOLOPT-SR-GTH,
450/60 Ry, 5.8 s/step on 1 `mpc` node).

## 1. What CP2K 2026.2 already supports (checked in the local data dir and source)

| need | available | where |
|---|---|---|
| r²SCAN-consistent PPs | `GTH-SCAN-q1/q4/q6`, Zr `GTH-SCAN-q12` | `POTENTIAL_UZH_2026.2` |
| r²SCAN-consistent bases | `DZVP/TZVP/TZV2P-MOLOPT-SCAN-GTH-q*` for H, C, O, Zr | `BASIS_MOLOPT_UZH_2026.2` |
| hybrid PPs | `GTH-PBE0-q*` for H, C, O, Zr | `POTENTIAL_UZH_2026.2` |
| hybrid bases | `*-MOLOPT-PBE0-GTH-q*` | `BASIS_MOLOPT_UZH_2026.2` |
| ADMM auxiliary bases | `admm-dzp-q*` for H, C, O, Zr (q12) | `BASIS_ADMM_UZH` |
| D3(BJ) parameters | built in for `R2SCAN`, `R2SCAN0`, `R2SCANH`, `R2SCAN50`, `PBE0`, `HSE06` | `src/qs_dispersion_utils.F` |

So no hand-entered dispersion parameters or borrowed PPs are needed. Use
**D3(BJ)**, not zero-damping D3: the r²SCAN-family parameters exist only for BJ
damping (Ehlert 2021; Bursch 2022).

## 2. Decision points

### 2a. "r²SCAN hybrid" is three functionals

| name | exact exchange | D3(BJ) key | comment |
|---|---|---|---|
| r²SCANh | 10 % | `R2SCANH` | cheapest hybrid, closest to r²SCAN |
| r²SCAN0 | 25 % | `R2SCAN0` | the natural partner to PBE0 — same HF fraction |
| r²SCAN50 | 50 % | `R2SCAN50` | over-opens gaps; rarely right for MOFs |

Recommendation: **r²SCAN0**, so the set is a clean 2×2:
{PBE, r²SCAN} base × {semilocal, 25 % hybrid}, with HSE06 as the screened
variant of PBE0.

### 2b. Periodic exact exchange in a small cell

- Primitive cell: fcc, a_prim ≈ 14.65 Å, perpendicular width ≈ 12 Å.
- **PBE0 and r²SCAN0** need `INTERACTION_POTENTIAL TRUNCATED` with
  `CUTOFF_RADIUS` < half the width → **≤ ~5.9 Å**, plus the long-range
  correction (`PBE_HOLE_T_C_LR` for PBE0). 5.9 Å is borderline; convergence
  of the gap with radius (4.5 → 5.9 Å) should be checked. The 456-atom
  conventional cell allows ~10 Å but costs ~4³≈60× more HFX per step.
- **HSE06** uses `SHORTRANGE` with ω = 0.11 bohr⁻¹ and decays fast — no
  truncation problem. It is the most robust hybrid for this cell.
- Γ-point only: UiO-66 has flat bands (wide-gap insulator), so Γ-sampling of
  the 114-atom cell is acceptable, but confirm the gap does not move by
  more than ~0.05 eV against the conventional cell once (GGA level is enough).

### 2c. ADMM + meta-GGA

ADMM (see GLOSSARY) computes exact exchange in a small auxiliary basis and
corrects the difference with a GGA exchange functional (`EXCH_CORRECTION_FUNC
PBEX`). For r²SCAN0 the correction is therefore PBE-exchange, not r²SCAN
exchange — an approximation, standard practice, but note it in the methods.
Use `ADMM_PURIFICATION_METHOD NONE` with `METHOD BASIS_PROJECTION` (the
recommended pairing for condensed phases). Run one small `--check` + a few SCF
steps locally to confirm 2026.2 accepts meta-GGA + ADMM.

### 2d. Grids for meta-GGA

r²SCAN is far less grid-sensitive than SCAN, but still more than revPBE.
Converge `CUTOFF` (450 → 600 → 800 Ry, `REL_CUTOFF` 60) on total energy
and forces before trusting anything. Budget 600 Ry as the likely answer.

### 2e. Keep pseudopotential and basis consistent per functional

| functional | PP | basis | ADMM aux |
|---|---|---|---|
| revPBE-D3 (baseline) | GTH-PBE | DZVP-MOLOPT-SR-GTH | — |
| r²SCAN-D3(BJ) | GTH-SCAN | DZVP-MOLOPT-SCAN-GTH | — |
| r²SCAN0-D3(BJ) | GTH-SCAN | DZVP-MOLOPT-SCAN-GTH | admm-dzp |
| PBE0-D3(BJ) | GTH-PBE0 | DZVP-MOLOPT-PBE0-GTH | admm-dzp |
| HSE06-D3(BJ) | GTH-PBE0 | DZVP-MOLOPT-PBE0-GTH | admm-dzp |

Mixing basis families between functionals adds a basis-set error on top of
the functional difference. If cleaner comparisons matter more than
PP-consistency, use one family (UZH DZVP-MOLOPT-PBE0) everywhere.

## 3. What to compute — the test should match the science

AIMD with every hybrid is not affordable: hybrid + ADMM is typically 5–20×
the GGA step, i.e. ~30–120 s/step here, so 1000 steps ≈ 1–4 node-days each.
Suggested protocol, cheapest first:

1. **Lattice + geometry** (cell opt, `GEO_OPT` + `CELL_OPT`): compare a_conv
   to experiment (~20.70–20.78 Å) and Zr–O distances. Hybrids: do r²SCAN0 and
   HSE06 only; PBE0 ≈ HSE06 for geometry.
2. **Band gap** at each functional's own geometry *and* at one fixed
   geometry (separates electronic from structural effects). Expect semilocal
   ~3.0 eV (underestimated); hybrids ~4.0–4.4 eV; optical gap ~3.9–4.1 eV.
   HSE06 < PBE0 by ~0.5 eV typically. Note: Γ HOMO–LUMO of the linker.
3. **CO₂ binding energy** (the mof-co2-capture goal): E(MOF+CO₂) −
   E(MOF) − E(CO₂) at the μ3-OH / tetrahedral cage site. This is where D3
   dominates, and the most useful number to compare.
4. **Optional:** short r²SCAN-D3(BJ) AIMD only if (1–3) show r²SCAN is
   better than revPBE-D3; it is ~1.5–2× the revPBE cost, the only option
   besides the baseline affordable for MD.

## 4. Practical order

1. Write 5 inputs from the single-point template, `cp2k_check_input` each,
   then a short HOKUSAI test job (never on the laptop).
2. Cutoff convergence for r²SCAN (one node, short).
3. HFX radius convergence for PBE0 (4.5/5.5/5.9 Å) — decides if the
   primitive cell is good enough for the 25 % hybrids.
4. Single-point timings → `cp2k_bench_*` records → `launch_plan` for the
   optimizations.
5. Production: protocol §3.

Open questions: which r²SCAN hybrid; which observables matter most (gap,
lattice, CO₂ binding); whether CO₂-loaded structures already exist under
`rccs-agentic-ai/mof-co2-capture/`.

## 5. Status (2026-11-04)

Decision: r²SCAN hybrid = **r²SCAN0**. A fifth input, PBE-D3(BJ) on the same
UZH family, completes the 2×2 {PBE, r²SCAN} × {semilocal, 25 % hybrid}.

- Inputs: `scratchpad/uio66-func/{pbe,r2scan,pbe0,hse06,r2scan0}/` (sp.inp +
  xc.inc + subsys.inc), generated by `make_inputs.sh` there. Structure:
  `uio66_prim_114_ch108.xyz` (24 C–H stretched to 1.08 Å). Cell widths
  11.97 Å, so `HFX_RADIUS` 5.9 Å fits.
- Settings: 600/60 Ry, ADMM `admm-dzp` + `BASIS_PROJECTION` / no
  purification / `PBEX` correction, HFX `EPS_SCHWARZ` 1e-6, D3(BJ) with the
  built-in key per functional.
- `cp2k --check`: all five pass (local, parse only).
- A local 3-SCF-step test was started by mistake and stopped (user: no CP2K
  runs on the laptop). What it showed before it ended: PBE and r²SCAN both
  run their XC at runtime (r²SCAN ~65 s/SCF step vs PBE ~20 s, 4×2 on the
  Mac); hybrids never reached the SCF. **The hybrid + ADMM (and r²SCAN0
  meta-GGA + ADMM) runtime path is still untested.**
- Next: upload to `~/agent/work/cp2k-runs/UiO-66-prim/functionals/<f>/`,
  `cp2k_check_input` on HOKUSAI, one short test job (MAX_SCF small) per
  hybrid to get s/SCF-step and HFX memory, then `launch_plan` for the full
  single points.

## 6. Tool audit: what rookery / yagura / hyogo did and did not cover

The point of this exercise is to do the work *through the tools* and log
where they fall short. Audit of every step so far (2026-11-04):

| step | tool used | verdict |
|---|---|---|
| fix X-ray C–H | yagura `structure_stretch_xh` | ✅ worked (24 moved). Needs the out folder to exist already (Errno 2) |
| check structure | yagura `structure_check` | ✅ worked, but I first **skipped** it (redone afterwards): clean, widths 11.97 Å |
| look up kinds | hpc-codes `cp2k_kinds` | ❌ **gap G1**. Hand-wrote the kinds instead |
| write `&SUBSYS` | hyogo `cp2k_make_subsys` | ⚠️ worked for the plain kinds; **gap G2** for ADMM (patched with sed) |
| write the inputs | none exists | ❌ **gap G3**. Hand-written bash generator |
| parse check | hpc-codes `cp2k_check_input` | ❌ **gaps G4, G5**. Local `--check` used instead (all 5 ok) |
| stage | hpc `fs_mkdir`, `fs_upload`, `fs_extract` | ⚠️ worked; **gap G6** |
| plan | hpc-codes `launch_plan` | ⚠️ ran; **gap G7** |

### Gaps to fix (proposed owner and fix)

G1, G2 and G3 were fixed on 2026-11-04 (not committed): `cp2k_kinds(family=,
aux_basis=)`, `AUX_FIT` in `cp2k_make_subsys`, and `cp2k_make_input`. See
[cp2k-make-input.md](cp2k-make-input.md).

- **G1 — `cp2k_kinds` is GTH-PBE + `BASIS_MOLOPT` only** (hpc-codes,
  `hpc_codes/cp2k/tools.py` `kinds`, `kinds.py`). Asked for
  `DZVP-MOLOPT-SCAN-GTH` or `-PBE0-GTH`, it returned errors for all four
  elements, though HOKUSAI's data dir has them in `BASIS_MOLOPT_UZH_2026.2`.
  Fix: a `family` argument (`pbe` | `scan` | `pbe0`) → the UZH basis and
  potential files with the `-q<N>` names, returning also the file names
  for `BASIS_SET_FILE_NAME`/`POTENTIAL_FILE_NAME`, and an optional
  `aux_basis` (e.g. `admm-dzp`) looked up in `BASIS_ADMM_UZH`.
- **G2 — `cp2k_make_subsys` writes no `BASIS_SET AUX_FIT`** (hyogo). Fix:
  accept `aux_basis` per kind (from G1) and emit the line.
- **G3 — no input writer beyond `&SUBSYS`** (hyogo). The single-point
  template and skill cover revPBE-D3 only and just say "hybrids or SCAN need
  other files and ADMM — ask first". Everything that is easy to get wrong
  was hand-written: libxc meta-GGA sections, `SCALE 0.75` for r²SCAN0,
  `PBE_HOLE_T_C_LR`, `XWPBE` for HSE06, ADMM settings, `t_c_g.dat`, the
  D3(BJ) key per functional. Fix: `cp2k_make_input(run_type, functional,
  ...)` in hyogo with presets for {revPBE, PBE, r²SCAN, PBE0, HSE06,
  r²SCAN0, r²SCANh} that also **checks HFX radius < min width / 2** (widths
  come from `cp2k_make_subsys`) and warns that r²SCAN0 has no long-range
  correction. Then a "functionals" section in `codes/cp2k/single-point/SKILL.md`.
- **G4 — `cp2k_check_input` dies at the transport, with a misleading
  message** (rookery). The tool allows `--check` 60 s on the login node,
  but remotemanager's default is `timeout` 5 s × `max_timeouts` 3 ≈ 30 s,
  after which it raises "could not communicate with process"; rookery then
  reports *"the connection failed — the settings are probably wrong"*. Any
  login-node command > ~30 s hits this. **Very likely the real cause of the
  "transient" bug 2 in [uio66-e2e.md](uio66-e2e.md).** Fix: give long tools
  their own timeout (or set `computer_defaults={"timeout": 20}` for
  hokusai), and classify a remotemanager timeout as "command too slow", not
  "unreachable". Workaround applied: `"computer": {"timeout": 20}` in
  `~/.hpc-agent/hokusai.json` (backup at `hokusai.json.bak`).
- **G5 — `--check` now hits its own 60 s limit with empty output**, even
  for `md/aimd_nvt.inp`, which passed this morning. So it is the login node
  or the build env, not these inputs. Not diagnosed: it needs a raw
  `run_command` (permission). Also a tool gap: on timeout `tail` is empty,
  so nothing says *where* it hung (env sourcing? MPI singleton init?).
  Fix: print a marker after `source` and capture partial output.
  **Diagnosed (2026-11-04, two approved login-node commands):** the env
  script loads in 0.5 s and finds `cp2k.psmp`; `timeout 60 cp2k.psmp
  --version` prints nothing and exits 124. So the binary hangs **before
  CP2K starts** — in MPI start-up (singleton init) on the login node, not in
  input parsing. It worked this morning. Suspects, not yet checked: stale
  MPI/PMIx daemons of mine left on the login node (e.g. from the five
  parallel `--check` calls killed by `timeout`), or a login-node MPI/network
  change. Next check (needs OK): `ps -u $USER -o pid,etime,cmd | grep -E
  'cp2k|prte|orted|pmix'`. Tool fix regardless: `cp2k_check_input` should
  run `--version` first with a short limit and report "MPI start-up hangs
  on the login node" instead of a bare timeout.
- **G6 — no directory upload.** `fs_upload` is one file per call; I used
  tar + `fs_extract`, and macOS tar shipped `._*` AppleDouble files into
  every folder. Fix: `fs_upload` of a folder (rsync -r), or set
  `COPYFILE_DISABLE=1` / skip `._*` in the skill's tar recipe.
- **G7 — `launch_plan` knows nothing about hybrids.** `match: none`, no
  estimate, and it proposes 16 ranks × 7 threads without considering HFX
  memory per rank (`MAX_MEMORY` × ranks vs node RAM). Fix: record hybrid
  single-point timings (after the test job) and let `launch_plan` warn on
  HFX memory.
- **G8 — no single-point output parser.** Energy, SCF convergence and the
  HOMO–LUMO gap will have to be grepped from `.out`. Fix: a
  `cp2k_sp_parse` (hpc-codes, like `cp2k_bench_parse`) returning energy,
  forces max, SCF steps, gap, timings.

### Not a tool gap (my process)

- Skipped `structure_check` and `cp2k_kinds` at first, and ran CP2K on the
  laptop. Both corrected; memory updated for the latter.
