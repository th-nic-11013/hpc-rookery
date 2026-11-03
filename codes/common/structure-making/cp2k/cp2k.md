# Structures for CP2K

Read the parent `SKILL.md` first. This assumes a checked extended XYZ
whose charge, multiplicity and periodicity the user has confirmed.

CP2K takes the structure in `&FORCE_EVAL / &SUBSYS`: the cell, the
coordinates, and a `&KIND` per element naming its basis set and
pseudopotential. Every input template in the CP2K run skills includes it
from one file:

```text
&SUBSYS
  @INCLUDE subsys.inc
&END SUBSYS
```

so one `subsys.inc` serves the single-point, optimization and AIMD inputs
alike, and the method settings never get mixed up with the structure.

## Make `subsys.inc`

`make_subsys.py` (next to this file) needs only numpy. Run it on the login
node, against the data folder of the CP2K build that will run the input:

```bash
cd ~/agent/work/<project>/structures/<system>
{{CP2K_PYTHON}} -I make_subsys.py UiO66-prim.xyz \
    --data-dir {{CP2K_DATA_DIR}}          # crystal / MOF / liquid
{{CP2K_PYTHON}} -I make_subsys.py mol.xyz --periodic NONE \
    --data-dir {{CP2K_DATA_DIR}}          # isolated molecule
```

It writes `subsys.inc` with the cell, the coordinates inline (`&COORD`),
and per element `DZVP-MOLOPT-SR-GTH` with the matching `GTH-PBE-q<N>`.
**`q` is read from the data files, never guessed:** Zr q12, Ti q12, Cu q11,
N q5. It stops with a clear message if an element has no such basis or
potential. In that case, list what exists
(`grep -E "^ *Fe " <data>/BASIS_MOLOPT`) and ask the user which to use.

It also prints:
- the cell's perpendicular widths, with a warning below 10 Å (poor Gamma-point sampling);
- the valence-electron count, with a warning when it is odd (open shell).

`--stretch-ch 1.08` fixes X-ray C-H bonds, and `--basis` chooses another
MOLOPT basis.

## Matching the potential to the functional

`GTH-PBE` potentials are the usual choice for PBE, revPBE and their D3
variants. revPBE-D3 with these potentials and `DZVP-MOLOPT-SR-GTH` is what
the {{DISPLAY_NAME}} benchmarks used (UiO-66, water). Hybrids, SCAN or all-electron work need other potential and
basis files. Ask the user, and look the names up in the data folder.

## Periodicity

- **`--periodic XYZ`** (default): crystals, liquids, MOFs, slabs with
  vacuum in the cell. For polar slabs, add
  `SURFACE_DIPOLE_CORRECTION .TRUE.` to `&DFT`.
- **`--periodic NONE`**: an isolated molecule in the box from
  `to_extxyz.py --vacuum`. The run input then needs its `&POISSON` block
  enabled (each template marks it):

  ```text
  &POISSON
    PERIODIC NONE
    PSOLVER  MT
  &END POISSON
  ```

  The Martyna-Tuckerman solver needs the box to be at least twice the
  molecule's extent. A neutral, non-polar molecule can instead stay
  periodic with ≥ 10 Å of vacuum, which is simpler.

## Charge and spin

These are set in `&DFT` of the run input, not in `subsys.inc`:

```text
CHARGE        0
MULTIPLICITY  1
UKS           .FALSE.
```

An odd valence-electron count, or any multiplicity > 1, needs
`UKS .TRUE.`.

## Check the input before any job

`cp2k.psmp --check` parses an input serially in seconds. It is fine on the
login node and proves the syntax, including the `@INCLUDE`, but not that
the SCF converges:

```bash
( source {{CP2K_DEFAULT_ENV}}; cd <run dir>; OMP_NUM_THREADS=1 timeout 60 cp2k.psmp --check -i <input>.inp )
```
