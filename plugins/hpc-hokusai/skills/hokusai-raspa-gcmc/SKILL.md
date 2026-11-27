---
name: hokusai-raspa-gcmc
description: Compute adsorption isotherm points (water or another guest in a rigid MOF/zeolite) with RASPA3 grand canonical Monte Carlo on HOKUSAI BigWaterfall2 (HBW2) - write one run folder per pressure (CB/CFCMC swap, Widom), adsorption from empty and desorption from a filled restart, continue runs that hit the time limit, and read loading, energies, acceptance and drift into an isotherm. Use whenever the user wants GCMC, an adsorption isotherm, uptake at a humidity or pressure, hysteresis between adsorption and desorption, a Henry coefficient or heat of adsorption - even if they only say "how much water does UiO-66 take up at 30 % RH".
---

# GCMC isotherm points with RASPA3 on HOKUSAI BigWaterfall2 (HBW2)

Every cluster tool call uses `facility="hokusai"`. Show the user the
points (P or P/P0, start state, cycles) and the cost, and wait for
permission before any submit.

## Contract

- **Reads:** the framework (extxyz with cell) and its charges; the
  pressures; for desorption, a filled restart (`output/restart_*.json`).
- **Writes:** `~/agent/work/raspa/<system>/<branch>/p<P/P0>/` on the
  cluster, one folder per point; locally the downloaded `output/` and the
  isotherm table.
- **Tools:** `raspa_make_input`, `raspa_parse_output` (hyogo; local),
  `fs_mkdir`, `fs_upload`, `render_job_script`, `submit_job`,
  `get_job_status`, `fs_tail`, `fs_download`.
- **Preconditions:** RASPA3 3.0.30 in `software_registry`; the
  user's approval.
- **Done when:** every point is reported with absolute loading ± error,
  `finished`, and its drift verdict; points still drifting are labelled
  "not equilibrated", never quoted as the isotherm.

Keywords and behaviour: the RASPA3 manual in the `lit-search-softwares`
library (`raspa3-commands`, `raspa3-restart`, `raspa3-faq`).

## 1. Decide

- **Box and cutoff.** Default: cutoff 12 Å and the smallest replication at
  least twice the cutoff wide (`raspa_make_input` picks it); the Ewald
  cutoff near half the box keeps the k-vector count sane (manual).
- **Pressure reference.** `p_over_p0` with `p0_pa` (3169 Pa: experimental
  water at 298 K). The model's own saturation pressure differs; say which
  P0 every table uses.
- **Branches.** Water in a hydrophobic MOF shows wide hysteresis: plain
  GCMC from empty stays empty past the step, and from full stays full.
  Run adsorption (from empty) and desorption (`restart_from` a filled
  run) and report both; if they never meet, the equilibrium step needs
  TMMC (`hokusai-raspa-tmmc`), not more cycles.
- **Cycles.** Initialization + equilibration (CFCMC needs the
  equilibration to fit its λ bias) + production. A filled box costs far
  more per cycle than an empty one (facility notes); size the time limit
  for the filled case.
- **Force field.** Defaults as the UiO-66 water campaign; changing charges,
  LJ or the water model is the user's call.

## 2. Write and run

```text
raspa_make_input(framework_path="uio66.extxyz", mode="gcmc", out_dir="ads/p0.30",
                 charges_path="uio66-RESP_CHARGES.resp", p_over_p0=0.30,
                 cycles=100000, init_cycles=20000, equil_cycles=20000)
raspa_make_input(framework_path="uio66.extxyz", mode="gcmc", out_dir="des/p0.30",
                 charges_path="uio66-RESP_CHARGES.resp", p_over_p0=0.30,
                 restart_from="<filled run>/output/restart_<T>_<P>.s0.json",
                 cycles=60000, init_cycles=5000, equil_cycles=15000)
```

One job per point or one job running several folders side by side (facility
notes). `render_job_script` (show it), ask, `submit_job`.

**Hit the time limit?** The run keeps writing `output/restart_*.json`
(every 5000 cycles, manual). Continue with a new folder whose
`restart_from` is that file, a short equilibration (the restart holds
positions only, not the λ bias) and the remaining production. Averages
restart from zero: combine the pieces' production averages weighted by
cycles, and say so.

## 3. Read

```text
raspa_parse_output(run_dir="ads/p0.30")
```

Quote `loading.<component>.absolute` (molecules/uc, mg/g) with its error;
`source` says final block averages or, for an unfinished run, the last
running average. Then:

- `n_trace`: first- vs second-half mean; a drift warning means not
  equilibrated.
- Excess loading needs `HeliumVoidFraction`; without it RASPA3 uses 1 and,
  near or above the model's vapour pressure, the Peng-Robinson bulk
  density can pick the liquid root and make excess meaningless (manual
  FAQ, UiO-66 campaign). Use absolute loading.
- `acceptance`: swap acceptance near zero at high loading means the
  insertion is stuck (CFCMC helps); translation/rotation outside 0.2-0.8
  is flagged.
- `energies.enthalpy_of_adsorption` only from an equilibrated point.

Report the isotherm as a table (P/P0, branch, absolute loading ± error,
cycles, finished, drift) and plot it if asked.

## HOKUSAI notes

- **Build:** RASPA3 3.0.30 (`software_registry`
  `raspa3-3.0.30-other-cpu-prebuilt`); `source
  ~/software/mlip/2026.10/raspa3/raspa3_env.sh`.
- **Layout used:** partition `mpc`, one point per job, 4 cores
  (`OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK`), 24 h limit; the UiO-66 water
  campaign's `run_point.sh` (rookery `notes/gcmc-uio66-water/`):
  `raspa3 > raspa3.stdout 2> raspa3.stderr` in the point's folder.
- **Measured (UiO-66, 2×2×2 box, TIP4P/2005, 12 Å, Ewald):** a near-empty
  point (P/P0 0.8 from empty, ~14 molecules) ran 20k+20k+100k cycles in
  55 min; a filled box (~950 molecules) runs ~2,700-4,400 cycles/h, so
  5k+15k+60k cycles do not fit 24 h (jobs 9825681-9825688 needed
  continuations 9854003-9854012). Worst case for 13 points at 24 h × 4 cores
  ≈ 830 core-h.
- Campaign folders: `~/agent/work/gcmc-uio66-water/raspa/{ads,des,des2,ext,seed}/`.
