# LAMMPS fixtures

Copies of runs that happened, for `tests/conformance.py`
(`hyogo_lammps_and_plumed_match_the_runs`, `yagura_lammps_data_and_fes_match_the_scripts`).

- `hokusai_bench/`: HOKUSAI `~/software/mlip/2026.10/bench/` (MLMD benchmark of
  MACE-MP-0 medium through LAMMPS `pair_style metatomic`), copied 2026-11-07:
  `inputs/*.in`, `parse_mlmd.py`, `prep_structures.py`, `run_lmp.sh`, `refs.tsv`,
  the uio66-prim and h2o-64 structure files, and two logs of job 9619420 stage 3
  (`h2o-64_f32_1x56_valid_r1`, `_time_r1`) with that stage's `status.tsv`.
  `structures/uio66_prim_114.xyz` is the source structure,
  `~/software/cp2k/bench/mof_structures/uio66_prim_114.xyz`.
- `hokusai_smoke/`: HOKUSAI `~/software/mlip/2026.10/tests/smoke/`: the LAMMPS–PLUMED
  coupling smoke test (`in.melt_plumed`, `plumed.dat`, its log and COLVAR).
- `lugano-6a/`: `~/Downloads/research/plumed_tutorial/lugano-6a/` (PLUMED masterclass
  LJ mixture with a moving restraint, run locally): inputs and `log.lammps`.
