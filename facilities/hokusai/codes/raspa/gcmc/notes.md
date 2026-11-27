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
