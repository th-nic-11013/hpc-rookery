---
name: {{SLUG}}-structure-making
description: Prepare, check, and stage atomic structures (molecules, crystals, MOFs, surfaces, liquids) for simulations on {{DISPLAY_NAME}}, for any simulation code - including turning a disordered or partially occupied CIF into a clean, ordered cell. Use whenever the user wants to build, convert, clean up, or sanity-check a structure, brings a CIF/XYZ/PDB/POSCAR/SMILES to run, or asks about partial occupancies, conventional vs primitive cells, or X-ray hydrogen positions. Code-specific conversion lives in a subfolder per code (cp2k/ for CP2K).
---

# Preparing structures for {{DISPLAY_NAME}}

This skill covers what every simulation code needs from a structure: get
it, make it a single ordered cell, check it, put it on the cluster.
Writing it in one code's input format is in that code's subfolder:

| code | read | then |
|---|---|---|
| CP2K | `cp2k/cp2k.md` | `{{SLUG}}-cp2k-single-point`, `-geo-opt` or `-aimd` |

When another code needs a converter, add a subfolder beside `cp2k/`. Keep
this file code-neutral.

## The common format: extended XYZ

Every tool here reads **extended XYZ with the cell on line 2**
(`Lattice="ax ay az bx by bz cx cy cz"`, rows are the cell vectors).
Isolated molecules get a box too.

## Where each tool runs

| tool | needs | where |
|---|---|---|
| `scripts/to_extxyz.py` (any format → extended XYZ) | ASE | the user's machine |
| `scripts/inspect_cif.py` (sites and occupancies of a CIF) | ASE | the user's machine |
| `examples/build_uio66.py` (worked MOF build) | ASE, spglib | the user's machine |
| `scripts/geomcheck.py` (checks) | numpy | anywhere, including the {{DISPLAY_NAME}} login node: `/usr/bin/python3.12 -I geomcheck.py x.xyz` |
| `cp2k/make_subsys.py` (CP2K &SUBSYS) | numpy and the CP2K data folder | the login node, next to the CP2K build |

{{DISPLAY_NAME}} has no Python or conda module, but the system
`/usr/bin/python3.12` (with numpy) runs these text tools on a login node.
Its default `python3` is 3.6, which is too old; always call `python3.12`
explicitly, with `-I`. Install ASE locally (`pip install ase spglib`)
rather than on the cluster.

## 1. Get the structure

Ask where it comes from; don't invent coordinates.

| source | how |
|---|---|
| user's file (CIF, XYZ, PDB, POSCAR) | `to_extxyz.py`. XYZ and PDB often lack a cell; ask for one or use `--vacuum` for a molecule |
| crystal / MOF from a database | the user downloads the CIF (CSD: RIKEN holds a licence, via the CCDC web search; COD; Materials Project). Note the entry and deposition number |
| small molecule by name or SMILES | RDKit or Open Babel to 3D (`obabel -:"<smiles>" --gen3d -O mol.xyz`), or `ase.build.molecule`, then `to_extxyz.py --vacuum 6` |
| bulk, surface, supercell | `ase.build.bulk`, `ase.build.surface` / `fcc111`, `atoms.repeat((n, m, k))` |
| liquid or solvated box | Packmol at the experimental density; say what density was used |

## 2. Make it one ordered cell (crystals and MOFs)

A deposited CIF is an average over many cells, not one real cell. **Read
`references/crystal-to-cell.md` before converting any CIF**, and for MOFs
work through `inspect_cif.py`:

- **Partial occupancies:**
  - two positions of one atom: keep the major one;
  - linker atoms below 1: vacancies, so force them to 1 for the ideal
    structure;
  - extra low-occupancy atoms (solvent, modulator): drop them;
  - mixed O/OH on symmetric sites: order them by hand.

  Converting naively with ASE counts every partial site as a full atom.
  CCDC 1405739 read with `ase.io.read` gives C192H128O288Zr24 instead of
  C192H112O128Zr24.
- **Settle the choices with the user** before building: ideal or
  as-deposited defects, node protonation state, cell size. They change the
  science.
- **Conventional or primitive cell:** a face-centred lattice has a primitive
  cell a quarter the size (UiO-66: 456 vs 114 atoms). Build both from one
  source. For Gamma-point DFT, every *perpendicular width* should be
  above ~10 Å, not just the cell lengths.
- **Manually placed sites need every centring translation** (F lattice: 4).
  Compare the atom count with Z × the primitive count.
- **X-ray hydrogens are short** (C-H ≈ 0.95 Å instead of ~1.08). Say so.
  Either relax them, or stretch them for a timing run
  (`cp2k/make_subsys.py --stretch-ch 1.08`).

`examples/build_uio66.py` is a complete worked build (ideal hydroxylated
UiO-66 from CCDC 1405739), and `references/uio66-1405739.md` records each
site decision and the numbers it must reproduce. Copy and adapt it for
other frameworks.

## 3. Check it

```bash
/usr/bin/python3.12 -I geomcheck.py structure.xyz
```

It prints composition, cell lengths, angles, volume and perpendicular
widths, and the shortest distance per element pair. It flags overlaps
below 0.7 Å (disorder left in, duplicated atoms) and C-H or N-H below
1.0 Å (X-ray H), and exits 1 if it flags anything. Also report, and check
against known values where they exist:

- the formula is the intended one and charge-neutral (or the intended charge);
- the density (ideal UiO-66: 1.240 g/cm³);
- for crystals: the space group (`spglib`), and a bond-length table by
  element pair.

Then ask the user what only they know: **charge**, **spin multiplicity**
and **periodicity**. `to_extxyz.py` prints which multiplicities the
electron count allows. Never guess these: a wrong charge or spin silently
gives a different system.

By eye:
- isolated molecules: ≥ 10 Å between periodic images, or a non-periodic setup;
- slabs: enough layers, ≥ 15 Å of vacuum, and a dipole correction if polar;
- supercells: no atom sees its own image within the interaction range;
- hydrogens: often missing from crystallographic and PDB files.

## 4. Stage it on {{DISPLAY_NAME}}

Keep each system together under
`~/agent/work/<project>/structures/<system>/`: the source file, the
extended XYZ, and a short `README` (source and deposition number, charge,
multiplicity, every edit made). One file per `fs_upload` call, with the
full remote path:

```text
fs_mkdir(facility="{{SLUG}}", path="~/agent/work/<project>/structures/<system>")
fs_upload(facility="{{SLUG}}", local_path="UiO66-prim.xyz",
          remote_path="~/agent/work/<project>/structures/<system>/UiO66-prim.xyz")
```

Report to the user: the files written, a table of cell, atom count and
formula, the check results, and caveats (unrelaxed geometry, short C-H,
experimental cell). Then hand over to the code's subfolder.
