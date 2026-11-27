## HOKUSAI notes

- **Build:** RASPA3 3.0.30 (`software_registry`
  `raspa3-3.0.30-other-cpu-prebuilt`); `source
  ~/software/mlip/2026.10/raspa3/raspa3_env.sh`.
- **Run script:** rookery `notes/gcmc-uio66-water/tmmc/run_windows.sh`
  runs every `w*/` folder in parallel, one core each, and copies
  `tmmc/tmmc_statistics.txt` to `tmmc/snap/<unix time>.txt` whenever it
  changes (the analysis subtracts an early snapshot).
- **Measured (UiO-66 water, 2×2×2 box):** ~2.5 ms per MC step at
  N ≈ 500-1000 (CBMC swap is half the moves; test job 9987057). Production
  job 10001962: 36 windows of 30 molecules (0-1080) on one 36-core `mpc`
  node, 16 GB, 24 h ≈ 860 core-h, 3.2×10⁷ steps per window (~30 bias
  updates).
- Analysis: rookery `notes/gcmc-uio66-water/tmmc/analyze_tmmc.py`
  (stitch, recompute ln Π from CM, reweight, equal-area pressure).
