# VASP inputs (fixtures for vasp_make_incar)

- `INCAR.label`: the INCAR of a real MXene + water single-point label
  (Cr2CO2, VASP 6.4.3, 2026-11), unchanged.
- `POSCAR`: a 10-atom stand-in with the label's species order (C Cr H O),
  not a real structure: only the species and counts lines matter here.
- `expected_*.INCAR*`: what the campaign's own script (`make_magmom_test2.py`,
  2026-11-05, functions `build_incar`, `pre_incar`, `release_incar`,
  `magmom_line`) wrote from those two files, NBANDS 660 and NCORE 12:
  - `expected_rel.INCAR` + `.INCAR.pre`: Cr 3.0, pre-run at NUPDOWN 64, then release (ISTART 1)
  - `expected_nup.INCAR`: Cr 2.5, NUPDOWN 60 (a half-metal: fixed moment)
  - `expected_damp.INCAR`: Cr 1.5 with damped mixing (AMIX 0.2, BMIX 1.0, AMIN 0.01, AMIX_MAG 0.4, BMIX_MAG 1.0)

The test compares settings (comments, layout and SYSTEM aside): the script
also rewrote a comment and the SYSTEM title, which the tool leaves alone.
The same comparison passed on all 23 runs of the campaign's MAGMOM test 2.
