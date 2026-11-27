---
name: {{SLUG}}-raspa-tmmc
description: Run transition-matrix Monte Carlo (TMMC) windows with RASPA3 on {{DISPLAY_NAME}} to get ln Pi(N) and the equilibrium adsorption step of water (or another guest) in a rigid framework - plan overlapping N windows, write one run folder per window (CBMC swap only), run them side by side, check each window's collection matrix and hand the statistics to the stitching analysis. Use whenever plain GCMC shows hysteresis or never fills, the user asks for the coexistence pressure, a free-energy profile in N, TMMC, or "where is the real water step" - even if they only say "the adsorption and desorption branches never meet".
---

# TMMC windows with RASPA3 on {{DISPLAY_NAME}}

Every cluster tool call uses `facility="{{SLUG}}"`. Show the user the window
plan (windows, steps per window, cores, wall time) and the cost, and wait
for permission before the submit.

## Contract

- **Reads:** the framework and charges (as for GCMC), the N range to cover
  (from the GCMC branches: 0 to past the filled loading), a reference
  pressure.
- **Writes:** `{{RASPA_WORK_DIR}}/<system>/tmmc/<run>/w<Nmin>-<Nmax>/` on
  the cluster; locally each window's `tmmc/tmmc_statistics.txt` (and
  snapshots, below) and the stitched result.
- **Tools:** `raspa_make_input`, `raspa_parse_output` (hyogo; local),
  `fs_mkdir`, `fs_upload`, `render_job_script`, `submit_job`,
  `get_job_status`, `fs_tail`, `fs_download`.
- **Preconditions:** RASPA3 {{RASPA_VERSION}}; GCMC branches that bound the
  N range; the user's approval.
- **Done when:** every window's collection matrix covers its whole window,
  the windows are stitched, and the coexistence pressure is reported with
  the P0 it is quoted against; or the uncovered windows are named.

Keywords: the manual's TMMC example (`raspa3-examples-advanced`, Tobacco-667)
in the `lit-search-softwares` library; RASPA3's own TMMC behaviour below
was read from the 3.0.30 source.

## 1. Decide

- **Windows.** Overlap neighbouring windows by one macrostate
  ([0, 30], [30, 60], ...). Narrow windows (~30 molecules) cross faster;
  one core per window.
- **Moves.** CBMC swap only (`SwapProbability`): the collection matrix
  needs the N -> N±1 acceptance, which plain and CBMC swaps report and
  CFCMC does not. No Widom, no thermodynamic integration.
  `raspa_make_input(mode="tmmc")` writes exactly that.
- **Start.** Each window starts at Nmin by CBMC insertion. Never from a
  restart: RASPA3 does not check a restart's N against the window
  (memory corruption); `raspa_make_input` refuses it.
- **Length.** Steps, not cycles: a cycle is max(20, N) steps, so convert
  a target step count per window with its mean N. The bias updates every
  10^6 steps (no input key), and the statistics file is written only then
  and at the end, so a run killed by the time limit loses what came after
  the last update; leave margin.
- **Reference pressure.** Any; ln Pi reweights exactly. Quote the result
  against the stated P0.

## 2. Write and run

```text
raspa_make_input(framework_path="uio66.extxyz", mode="tmmc", out_dir="tmmc/prod/w0030-0060",
                 charges_path="uio66-RESP_CHARGES.resp", p_over_p0=0.3,
                 window=[30, 60], cycles=<steps / max(45, 20) - init>, init_cycles=<5 %>,
                 equil_cycles=0)
```

One job runs every window folder side by side (one core each) and snapshots
`tmmc/tmmc_statistics.txt` whenever it changes: RASPA3 cannot zero the
collection matrix after initialization, so the analysis subtracts an
early snapshot. The facility notes have the run script.

## 3. Check and stitch

Download each window's `tmmc/` (with `snap/`) and `output/`, then per window:

```text
raspa_parse_output(run_dir="tmmc/prod/w0030-0060")
```

- `tmmc.visited_range` covers the whole `window` (`visited` =
  `macrostates`); an uncovered window needs more steps, not a wider one.
- `tmmc.matrix_updates` ≥ a few; one update means the bias barely moved.
- The `lnpi` column in the file is stale (last bias update): recompute ln
  Pi from the collection matrix.

Stitching windows at their shared macrostate, reweighting to other
pressures and the equal-area coexistence pressure are analysis (the
campaign's `analyze_tmmc.py`; a yagura tool is the logged gap). Report the
coexistence P/P0, the loading of each phase and which windows limit the
precision.

{{CODE_NOTES}}
