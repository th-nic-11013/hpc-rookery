# hyogo inventory

Every hyogo tool, live or planned, with its category. The
`hyogo-intake` skill (`.claude/skills/hyogo-intake/`) adds rows here when
it adapts a skill or script into tools; `tests/conformance.py` checks that
the `live` rows are exactly the tools `hyogo-mcp` serves.

## What belongs in hyogo

hyogo runs simulation methods and writes their inputs. It never reaches a
cluster and imports nothing from rookery or yagura; facts from them come
in as arguments the agent passes along. Anything else goes to its owner:

| the unit... | owner |
|---|---|
| needs SSH, a facility, a build, a benchmark, a layout or a cost | rookery (`hpc_codes/`, `facilities/<slug>/`) |
| makes, checks, fixes or converts a structure | yagura |
| analyses physics in a trajectory or output (RDF, MSD, spectra, ...) | yagura (`yagura/analysis/`) |
| defines a record or file format shared by every repo | densho (agent-notes) |
| is judgment, an approval or an explanation for the user | stays prose in the skill |

## Categories

Each tool has one **method**, one **stage** and one **code**.

**method**: the science it serves.

| method | covers |
|---|---|
| `any` | shared by several methods (e.g. the &SUBSYS block) |
| `sp` | single-point energies and forces |
| `opt` | geometry and cell optimisation |
| `md` | molecular dynamics, ab initio or classical |
| `sampling` | enhanced sampling (metadynamics, umbrella, ...) |
| `mlip` | training or fine-tuning ML interatomic potentials |
| `al` | active-learning loops (pick, label, retrain) |

**stage**: what it does in a run's life.

| stage | does | not this (owner) |
|---|---|---|
| `input` | writes a run's input files | the structure itself (yagura) |
| `check` | method-level sanity before a run (HFX radius vs cell, functional presets) | parse check on the build (rookery `cp2k_check_input`) |
| `restart` | continues or extends a run from its own outputs | resubmitting (rookery `submit_job`) |
| `parse` | reads run-control facts from outputs: converged, steps done, energy drift | physics analysis (yagura) |
| `select` | picks frames, configurations or data (datasets, AL) | |

**code**: `cp2k`, `lammps`, `mace`, ... or `none` for code-neutral tools.

Naming and place: `<code>_<verb>_<object>` (`cp2k_make_subsys`), or
`<method>_<verb>_<object>` when code is `none`. Logic lives in
`hyogo/<code>/<module>.py` (or `hyogo/<method>/` when code-neutral); the
MCP wrapper lives in `hyogo/server.py`.

## Tools

`status`: `live` (served by `hyogo-mcp`), `planned` (a plan
was agreed), `candidate` (seen, not yet planned).

| tool | method | stage | code | module | status | source | notes |
|---|---|---|---|---|---|---|---|
| `cp2k_make_subsys` | any | input | cp2k | `hyogo/cp2k/subsys.py` | live | `codes/common/structure-making/cp2k/make_subsys.py` | split of `make_subsys.py`: kinds and data files → rookery `cp2k_kinds`, checks → yagura `structure_check`, `&SUBSYS` writing → here |
| `cp2k_make_input` | any | input | cp2k | `hyogo/cp2k/input.py` | live | `codes/cp2k/*/templates/`, UiO-66 `make_inputs.sh` | templates and UiO-66 functional presets as one tool; includes the HFX radius check |
| `cp2k_parse_output` | any | parse | cp2k | `hyogo/cp2k/output.py` | live | hand greps of HOKUSAI UiO-66 outputs (sp, cell_opt); patterns shared with `hpc_codes/cp2k/parse_bench.py` | G15: reads a local .out (fs_download first) into run-control facts (finished, SCF/opt convergence and criteria, walltime stop), printed results (energy, gap, forces, stress, final cell) and cost facts (timers, peak and HFX memory); writes nothing. Turning cost into a benchmark record stays rookery's |
| `vasp_make_incar` | any | input | vasp | `hyogo/vasp/incar.py` | live | MXene+water labelling campaign `make_magmom_test2.py` (G32) | edits a base INCAR in place (layout and comments kept, so a comment can go stale): MAGMOM per element in POSCAR order plus per-site overrides, NUPDOWN, NCORE/KPAR, NBANDS pinned (or read from a reference OUTCAR) and refused when it isn't a multiple of the band groups, constrain-then-release `INCAR.pre`; refuses MAGMOM/NUPDOWN with ISPIN 1 and a MAGMOM that doesn't count every atom; matched the script's settings on all 23 MAGMOM test 2 runs |
| `vasp_parse_output` | any | parse | vasp | `hyogo/vasp/output.py` | live | campaign `outcar_to_extxyz.py`, `audit_completed.py`, `compare_magmom_test.py` (G32) | OUTCAR (.gz ok) + OSZICAR + POSCAR beside it: finished, SCF per ionic step with VASP's EDIFF-reached line (NELM hits exit 0), converged = every SCF converged + TOTEN + full force block, energies, E_F, total and per-site moments (LORBIT >= 10) with flipped-site counts, forces, stress, layout (ranks, KPAR, NCORE, band groups, NBANDS), timing, VASP's message boxes; NUL holes stripped, POSCAR fills a lost header; agreed with the campaign converter on 1524/1525 real OUTCARs. Physics diagnostics (where a moment sits) stay yagura's (G37); comparing two results (G38) is not here |
| `vasp_check_incar` | any | check | vasp | `hyogo/vasp/litsearch_check.py` | live | G41 | an INCAR against the VASP documentation, by calling lit-search (`reference check VASP --json`, the VASP wiki's INCAR tags kept as verified records) as a separate program: unknown tags with close matches, values outside a tag's type or words, tags newer than `vasp_version`, tags set twice; `vasp_make_incar` runs it on every INCAR it writes. Skipped, and says so, without lit-search (`HYOGO_LITSEARCH`, `HYOGO_LITSEARCH_LIBRARY`). `[string]` tags (ALGO) have no word list |
| `cp2k_restart_md` | md | restart | cp2k | `hyogo/cp2k/restart.py` | candidate | `codes/cp2k/aimd/SKILL.md` (24-hour restarts) | |

Changes to a live tool (not new rows): `aux_basis` per kind in
`cp2k_make_subsys`, done with `cp2k_make_input`. Done (G18):
`symmetry` in `cp2k_make_subsys` (`&CELL SYMMETRY`, checked against the
cell); `opt` keys `max_force`, `rms_force`, `keep_angles`, `keep_symmetry`
in `cp2k_make_input`; and a `@SET RESTART` / `&EXT_RESTART` block in every
geo_opt, cell_opt and md_nvt input, so a run continues after a walltime stop.
