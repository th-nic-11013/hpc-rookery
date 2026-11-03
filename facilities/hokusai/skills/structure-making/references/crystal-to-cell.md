# Turning a crystal structure into a benchmark cell

> From the rccs-agentic-ai `hokusai-hpc-cp2k-benchmark` skill (UiO-66 set up on
> 2026-10-16). Paths such as `~/software/cp2k/bench/...` are on HOKUSAI. In this
> repo `geomcheck.py` is in `../scripts/`, and `--stretch-ch` is in
> `../cp2k/make_subsys.py` as well as the benchmark's `make_cp2k_md_input.py`.

Read this before building a benchmark input from a CIF (CSD, CoRE MOF, Materials
Project) or any experimental structure. The examples are from UiO-66, set up on
2026-10-16 in `~/software/cp2k/bench/mof_structures/`.

## Where structures come from

- **CSD (Cambridge Structural Database).** RIKEN holds a licence, so the user can
  download CIFs from the CCDC web search (`https://www.ccdc.cam.ac.uk/structures/`)
  on their own machine. UiO-66 is the RUBTAK family: RUBTAK (Cavka et al. 2008),
  RUBTAK04 = deposition 1405739, Trickett et al. 2015, "synthesised in formic
  acid:DEF 0.5:1", Fm-3m, a = 20.7343 Å, flagged DISORDER. Modulated syntheses
  (formic/acetic acid) often carry missing-linker defects; prefer the cleanest,
  fully ordered entry and say which one was used.
- **Functionalised variants (UiO-66-NH2 and similar)** rarely have a usable
  deposited CIF (H atoms missing, disordered substituents). Build them from the
  parent framework: replace one ring H per linker with the group, then optimise.
- **Copying files to HOKUSAI:** from the Mac with the `hokusai` alias,
  `/usr/bin/scp FILE hokusai:~/<path>/` (system scp: the ssh config uses
  `UseKeychain`). Login hosts are hokusai1-4.riken.jp; `/home`
  is shared, so any of them works.

## Conventional vs primitive cell

A face-centred (F) lattice has a primitive cell one quarter the size: vectors
(0, a/2, a/2), (a/2, 0, a/2), (a/2, a/2, 0), length a/√2, angles 60°.

| UiO-66 cell | Atoms | Cell | Perpendicular width |
|---|---|---|---|
| Conventional | 456 (Zr24 O128 C192 H112) | cubic 20.734 Å | 20.73 Å |
| Primitive | 114 (Zr6 O32 C48 H28) | 14.661 Å, 60° | 11.97 Å |
| Primitive UiO-66-NH2 | 126 | about the same | |

A 114-atom cell is the size class of H2O-32/H2O-64 and suits a single-node layout
scan; the 456-atom cell is H2O-128-sized and suits a validation stage or a
multi-node test. Gamma-point sampling wants every perpendicular width (not just the
cell lengths) above about 10 Å; `geomcheck.py` and `make_cp2k_md_input.py` print
them.

## Disorder

CIFs of MOFs and oxides often put two chemical states on shared, partly occupied
sites. In UiO-66 (Fm-3m) the 8 μ3-O sites per Zr6 cluster are O/OH mixed and the
hydroxyl H is half occupied. A DFT model must be ordered: Zr6O4(OH)4 with OH on
four alternating faces (Td, space group F-43m, still face-centred), or the
dehydroxylated Zr6O6. Converting a disordered CIF naively (e.g. ASE) leaves
overlapping half-atoms. Check the result:

- composition per cell (UiO-66 primitive: Zr6 O32 C48 H28);
- `geomcheck.py`: no distance below 0.7 Å;
- in the ordered UiO-66 file: 4 μ3-OH with Zr-O 2.271 Å and O-H 0.998 Å, 4 μ3-O
  with Zr-O 2.063 Å, the 4 OH oxygens forming a regular tetrahedron
  (all six O···O 4.017 Å).

## X-ray hydrogen positions

X-ray diffraction places H at the bonding electron density, so C-H comes out near
0.95 Å (UiO-66 file: 0.949 Å) instead of the ~1.08 Å a DFT optimisation gives.
Starting MD from compressed C-H bonds puts large forces on the first steps.
`make_cp2k_md_input.py --stretch-ch 1.08` moves each H along its C-H bond and
nothing else; `geomcheck.py` flags C-H and N-H below 1.0 Å. A cell-fixed GEO_OPT
before production MD is still the better start; for a timing benchmark the
stretch is enough.

## Settings used for UiO-66 (the user's choice)

revPBE + D3 (zero damping; `&PBE PARAMETRIZATION REVPBE`, `TYPE DFTD3`,
`REFERENCE_FUNCTIONAL revPBE`, `dftd3.dat`), DZVP-MOLOPT-SR-GTH on all elements,
GTH-PBE-q12/q6/q4/q1 for Zr/O/C/H, CUTOFF 450, REL_CUTOFF 60, OT DIIS with
FULL_SINGLE_INVERSE, EPS_SCF 1e-6, MAX_SCF 30 with OUTER_SCF 10. That SCF works for
the 114-atom cell but stalled for the 456-atom cell; there, use OT DIIS with
PRECONDITIONER FULL_ALL and ENERGY_GAP 0.05 (converged in 55 steps; gap 3.16 eV,
energy per Zr6 unit within 0.5 mHa of the 114-atom cell). 484 valence
electrons, closed shell. Keyword names were checked in the 2026.2 source
(`src/input_cp2k_xc.F`, `src/qs_dispersion_utils.F`), and the input passed
`cp2k.psmp --check`.
