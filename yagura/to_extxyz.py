#!/usr/bin/env python3
"""Convert any structure ASE can read into extended XYZ with the cell on line 2,
the format every other tool in this skill reads (geomcheck.py, cp2k/make_subsys.py).

    python to_extxyz.py structure.cif out.xyz [--charge 0]
    python to_extxyz.py molecule.xyz out.xyz --vacuum 6      # isolated molecule: build a box

Needs ASE (pip install ase): run it on your own machine, then upload out.xyz.
It converts and reports. It does not fix anything: a CIF with partial
occupancies must be resolved first (references/crystal-to-cell.md), or every
partial site comes out as a full atom. yagura's structure_to_extxyz is
convert() below.
"""
import argparse
import sys

from ase.io import read, write


def convert(structure: str, out: str, vacuum: float | None = None, charge: int = 0,
            index: str = "-1") -> dict:
    """Write `out`; return what was written. ValueError if there is no full cell."""
    atoms = read(structure, index=index)
    if vacuum is not None:
        atoms.center(vacuum=vacuum)
        atoms.pbc = True
    if atoms.cell.rank < 3:
        raise ValueError(f"{structure} has no complete cell: give one in the source file, or use "
                         "--vacuum for an isolated molecule")
    atoms.pbc = True
    atoms.wrap()

    # Positions and the cell only: other tools here read exactly
    # "symbol x y z" after the Lattice line.
    write(out, atoms, format="extxyz", columns=["symbols", "positions"],
          write_info=False)

    electrons = int(atoms.get_atomic_numbers().sum()) - charge
    return {"out": out, "natoms": len(atoms), "formula": atoms.get_chemical_formula(),
            "cell": {"lengths": [round(float(x), 4) for x in atoms.cell.lengths()],
                     "angles": [round(float(x), 3) for x in atoms.cell.angles()]},
            "electrons": electrons, "charge": charge,
            "multiplicities": "1, 3, 5, ..." if electrons % 2 == 0 else "2, 4, 6, ..."}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("structure")
    ap.add_argument("out")
    ap.add_argument("--vacuum", type=float,
                    help="centre the atoms in a box with this much vacuum (Å) on every side")
    ap.add_argument("--charge", type=int, default=0,
                    help="net charge, for the electron-parity line")
    ap.add_argument("--index", default="-1",
                    help="frame of a multi-frame file (ase index syntax)")
    a = ap.parse_args()

    try:
        r = convert(a.structure, a.out, a.vacuum, a.charge, a.index)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"wrote {a.out}: {r['formula']} ({r['natoms']} atoms)")
    print(f"all-electron count {r['electrons']} at charge {a.charge:+d}: "
          f"spin multiplicity must be one of {r['multiplicities']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
