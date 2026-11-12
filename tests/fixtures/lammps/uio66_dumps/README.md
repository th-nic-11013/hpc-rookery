# UiO-66 MLMD dump excerpts

Frames cut verbatim (gzipped) from LAMMPS dumps on HOKUSAI, for
`yagura_lammps_dump_and_framework_check` (gap G42). Types are H C O Zr
(`pair_coeff * * dispersion/d3 H C O Zr`); timestep 0.5 fs; 456 atoms.

- `a2_600K_excerpt.dump.gz`: `~/agent/work/mlmd-uio66/a2-npt/runs/9748358/npt_600K/frames.dump`
  (A2, MACE-MP-0 + D3, NPT 1 bar 600 K, 801 frames), frames 0, 533–537 and 800.
  Frame 534 (step 26700) has 4 of the 192 Zr–O bonds above 2.7 Å, the run's
  maximum. Over the whole run, framework_check gives 353 of 801 frames with a
  Zr–O > 2.7 Å, at most 4 at once, longest excursion 6 frames, nothing dropped.
- `o2_1gpa_excerpt.dump.gz`: `~/agent/work/mlmd-uio66/o2-npt-1gpa/runs/9749291/frames.dump`
  (O2, MACE-MP-0 + D3, NPT 1 GPa 300 K, the collapse; 49 frames), frames 0, 16,
  32 and 48 (V 9040 → 4832 Å³). Frame 32 has the μ3-OH proton shared with a
  second O (O–H 1.07 / 1.30 Å, O···O 2.36 Å). Over the whole run: nothing dropped,
  12 persistent Zr–O stretches, shared H in 5 frames.
