#!/usr/bin/env python3
"""Convert any structure ASE can read into extended XYZ with the cell on line 2,
the format every other tool in this skill reads (geomcheck.py, cp2k/make_subsys.py).

    python to_extxyz.py structure.cif out.xyz [--charge 0]
    python to_extxyz.py molecule.xyz out.xyz --vacuum 6      # isolated molecule: build a box

Needs ASE (pip install ase): run it on your own machine, then upload out.xyz.
It converts and reports. It does not fix anything: a CIF with partial
occupancies must be resolved first (references/crystal-to-cell.md), or every
partial site comes out as a full atom.
"""
import argparse
import sys

from ase.io import read, write


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

    atoms = read(a.structure, index=a.index)
    if a.vacuum is not None:
        atoms.center(vacuum=a.vacuum)
        atoms.pbc = True
    if atoms.cell.rank < 3:
        print(f"{a.structure} has no complete cell: give one in the source file, or use "
              "--vacuum for an isolated molecule", file=sys.stderr)
        return 1
    atoms.pbc = True
    atoms.wrap()

    # Positions and the cell only: other tools here read exactly
    # "symbol x y z" after the Lattice line.
    write(a.out, atoms, format="extxyz", columns=["symbols", "positions"],
          write_info=False)

    electrons = int(atoms.get_atomic_numbers().sum()) - a.charge
    allowed = "1, 3, 5, ..." if electrons % 2 == 0 else "2, 4, 6, ..."
    print(f"wrote {a.out}: {atoms.get_chemical_formula()} ({len(atoms)} atoms)")
    print(f"all-electron count {electrons} at charge {a.charge:+d}: "
          f"spin multiplicity must be one of {allowed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
