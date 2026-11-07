# VASP outputs (fixtures for vasp_parse_output)

Real VASP 6.4.3 (`vasp_gam`, 48 MPI ranks, NCORE 4) outputs of an isolated
Cr atom in a 16.0 x 16.5 x 17.0 A box, PBE, ENCUT 415, ISPIN 2 with the
moment fixed by NUPDOWN: the E0 reference runs of an MXene + water
labelling campaign (2026-11). OUTCARs gzipped (`gzip -9 -n`) unchanged;
OSZICAR, POSCAR and INCAR copied unchanged.

| folder | run | what it tests |
|---|---|---|
| `cr-nup6/` | NUPDOWN 6, converged in 24 SCF steps | a clean single point: energies, forces, stress, layout, timing, an ADVICE box |
| `cr-nup4/` | NUPDOWN 4, hit NELM 300 | the NELM case: VASP exits 0 and prints energy and forces anyway |

Neither has LORBIT set, so neither has per-site moments; the moment block
was validated on 1525 real OUTCARs of the campaign, which aren't published.
The expected values in `tests/conformance.py` were read from these files by
hand. NUL holes and cut files are made from them inside the test. Don't edit.
