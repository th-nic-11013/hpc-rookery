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

Three tools, one per owner: the build's facts come from the cluster, the
text is written here, and the file goes up next to the run input.

1. **The kinds, from the build that will run the input:**
   ```text
   cp2k_kinds(facility="hokusai", elements=["Zr", "O", "C", "H"])   # build_id= for a non-default build
   ```
   For each element, `q`, the basis (`DZVP-MOLOPT-SR-GTH`, or `basis=`)
   and the matching `GTH-PBE-q<N>`, read from that build's own data files
   on the login node. **`q` is never guessed:** Zr q12, Ti q12, Cu q11,
   N q5. An element with no such basis or potential is in `errors`. Then
   list what exists (`grep -E "^ *Fe " <data_dir>/BASIS_MOLOPT`, with
   `data_dir` from the result) and ask the user which to use.
2. **The file, on this machine**, from the checked xyz:
   ```text
   cp2k_make_subsys(xyz_path="UiO66-prim.xyz", kinds=<step 1's result>)          # crystal / MOF / liquid
   cp2k_make_subsys(xyz_path="mol.xyz", kinds=<...>, periodic="NONE")           # isolated molecule
   ```
   It writes `subsys.inc` beside the xyz (or `out_path`) with the cell,
   the coordinates inline (`&COORD`) and one `&KIND` per element, and
   returns the electron count and `warnings`: a cell width below 10 Å
   (poor Gamma-point sampling), an odd electron count (open shell),
   PERIODIC NONE (enable `&POISSON`). Read them to the user.
3. `fs_upload` `subsys.inc` into the run folder.

Short X-ray C-H bonds are fixed before step 2, on the xyz:
`structure_stretch_xh(path, out_path, length=1.08)` (`structure_check`
flags them as `short_xh`).

**By hand, without the hyogo and yagura servers:** `make_subsys.py` (next
to this file, numpy only) does all three on the login node, against the
build's `data_dir` from `software_registry` (see `hokusai-records`):

```bash
cd ~/agent/work/<project>/structures/<system>
/usr/bin/python3.12 -I make_subsys.py UiO66-prim.xyz --data-dir <data_dir> [--stretch-ch 1.08]
/usr/bin/python3.12 -I make_subsys.py mol.xyz --periodic NONE --data-dir <data_dir>
```

It writes the same file and prints the same warnings.

## Matching the potential to the functional

`GTH-PBE` potentials are the usual choice for PBE, revPBE and their D3
variants. revPBE-D3 with these potentials and `DZVP-MOLOPT-SR-GTH` is what
the HOKUSAI BigWaterfall2 (HBW2) benchmarks used (UiO-66, water). Hybrids, SCAN or all-electron work need other potential and
basis files. Ask the user, and look the names up in the data folder.

## Periodicity

- **`--periodic XYZ`** (default): crystals, liquids, MOFs, slabs with
  vacuum in the cell. For polar slabs, add
  `SURFACE_DIPOLE_CORRECTION .TRUE.` to `&DFT`.
- **`--periodic NONE`**: an isolated molecule in the box from
  `structure_to_extxyz(..., vacuum=6)`. The run input then needs its `&POISSON` block
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

```text
cp2k_check_input(facility="hokusai", input_path="<run dir>/<input>.inp")
```
