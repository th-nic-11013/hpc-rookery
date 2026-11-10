# PLUMED fixtures

From `~/Downloads/research/plumed_tutorial/` (PLUMED masterclass 21.4 exercises,
run locally), copied 2026-11-07, for `tests/conformance.py`:

- `master-ISDD-1/`: GYRATION and COORDINATION on GB1 CA atoms; TORSION with MOLINFO.
- `master-ISDD-2/`: well-tempered METAD on alanine dipeptide's phi (`plumed.dat`), its
  reweighting input (`plumed_reweight.dat`), and Max Bonomi's `do_block_fes.py`, which
  yagura's `colvar_fes` reproduces.

From HOKUSAI `~/agent/work/mlmd-uio66/o1-opes-smoke/plumed.dat`, copied 2026-11-09 (G40):

- `o1-opes-expanded/plumed.dat`: the hand-written OPES_EXPANDED + ECV_MULTITHERMAL_MULTIBARIC input
  of the UiO-66 O1 run (MACE-MP-0 + D3 in LAMMPS, 300–600 K, 1 bar–1 GPa), which ran (job 9749278).
  `plumed_make_input` reproduces it with pressures given in bar.
