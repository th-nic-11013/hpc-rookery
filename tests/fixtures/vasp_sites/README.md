# VASP per-site outputs (fixtures for vasp_parse_output's sites_out and sites_by_environment)

`cr2co2-water/`: one real single point of an MXene + water labelling
campaign (2026-11): Cr2CO2 slab with 88 O, 128 H, 12 C, 24 Cr (252 atoms),
VASP 6.4.3, PBE, ENCUT 415, ISPIN 2, LORBIT 10, run from a constrained
pre-run's WAVECAR (MAGMOM test 2, "rel"). OUTCAR gzipped (`gzip -9 -n`)
unchanged; POSCAR, OSZICAR and INCAR copied unchanged.
`vasprun.xml.gz`: the same run's vasprun.xml with its `<projected>` block
(per band and k-point projections, 1.7 M lines) cut out and nothing else
changed, then `gzip -9 -n`; NEDOS 301, so a 191 meV grid.

The expected numbers in `tests/checks/yagura/yagura_sites_by_environment_matches_the_campaign.py`
are what the campaign's own scripts printed for this frame (2026-11-11):

- `stray_moment.py`: `Cr30:+3.08(nH=0,dM=0.00)  Cr14:+3.07(nH=0,dM=0.00)  Cr27:+2.98(nH=0,dM=0.00)`
- `water_charge.py`: c 31.16 A, E_F 2.623, water-O charge 5.2222 (p 3.6474),
  slab-O charge 5.0809, sum |m| on water O 0.012, |mag| 64.00
- `label_spins.py`: mag 64.0, 0 metal sites below -0.5 uB, mean positive metal moment 2.68
- `water_pdos.py` (on the uncut vasprun.xml.gz): `E_F=2.623  water O=55  grid=191 meV`;
  `spin 1: states/Ow in [-0.5,0]=0.0003  [0,+0.5]=0.0005  top of water-O PDOS (>0.05/eV) at E-E_F=-3.44 eV`;
  `spin 2: states/Ow in [-0.5,0]=0.0000  [0,+0.5]=0.0004  top of water-O PDOS (>0.05/eV) at E-E_F=-3.44 eV`
  (checked in `tests/checks/yagura/yagura_pdos_read_reads_vasp_by_environment.py`)

Don't edit.
