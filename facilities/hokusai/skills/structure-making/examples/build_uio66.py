"""Build ideal, hydroxylated UiO-66 (Zr6O4(OH)4(BDC)6) from CCDC 1405739.

The deposited structure is disordered and defective. Resolution used here:
  - linker (O1A, C1, C2, C3, H3): major positions, occupancy forced to 1
  - minor carboxylate O1B, formate/solvent O3: dropped
  - mu3 faces: 4 O + 4 OH in a tetrahedral pattern (F-43m); O at x=0.0509,
    OH at x=0.0685, H at x=0.0963 along the (111) diagonal (O2A/O2B/H2B)

Outputs (structures/): conventional cubic cell (456 atoms) and fcc primitive
cell (114 atoms), each as CIF and extended XYZ.

Run: .venv/bin/python build_uio66.py [--out DIR]   (default DIR = ./structures)
"""
import argparse
from itertools import product
from pathlib import Path

import numpy as np
import spglib
from ase import Atoms
from ase.build import cut, sort
from ase.io import write
from ase.neighborlist import neighbor_list
from ase.spacegroup import crystal

A = 20.7343  # experimental cell edge (Angstrom), CIF value

# asymmetric unit, Fm-3m (225): fractional coordinates from the CIF
BASIS = [
    ("Zr", (0.11957, 0.0, 0.0)),
    ("O", (0.1713, 0.0938, 0.0)),   # O1A, carboxylate O
    ("C", (0.1518, 0.1518, 0.0)),   # C1, carboxylate C
    ("C", (0.2029, 0.2029, 0.0)),   # C2, ring C (ipso)
    ("C", (0.2661, 0.1858, 0.0)),   # C3, ring CH
    ("H", (0.2780, 0.1416, 0.0)),   # H3
]
X_O, X_OH, X_H = 0.0509, 0.0685, 0.0963  # mu3 sites along (111)


def build_conventional() -> Atoms:
    atoms = crystal(
        symbols=[s for s, _ in BASIS],
        basis=[p for _, p in BASIS],
        spacegroup=225,
        cellpar=[A, A, A, 90, 90, 90],
        primitive_cell=False,
    )
    extra_sym, extra_pos = [], []
    for signs in product((1, -1), repeat=3):
        s = np.array(signs)
        if np.prod(s) > 0:  # oxide tetrahedron
            extra_sym.append("O")
            extra_pos.append(s * X_O)
        else:  # hydroxide tetrahedron, H pointing out
            extra_sym += ["O", "H"]
            extra_pos += [s * X_OH, s * X_H]
    centering = [(0, 0, 0), (0, .5, .5), (.5, 0, .5), (.5, .5, 0)]  # F lattice
    syms = extra_sym * len(centering)
    pos = np.vstack([np.array(extra_pos) + t for t in centering]) % 1.0
    mu3 = Atoms(syms, scaled_positions=pos, cell=atoms.cell, pbc=True)
    return sort(atoms + mu3)


def build_primitive(conv: Atoms) -> Atoms:
    prim = cut(conv, a=(0, 0.5, 0.5), b=(0.5, 0, 0.5), c=(0.5, 0.5, 0))
    prim.wrap()
    return sort(prim)


def report(name: str, atoms: Atoms) -> None:
    ds = spglib.get_symmetry_dataset(
        (atoms.cell[:], atoms.get_scaled_positions(), atoms.numbers), symprec=1e-3)
    i, j, dist = neighbor_list("ijd", atoms, 2.6)
    sym = np.array(atoms.get_chemical_symbols())
    print(f"\n== {name}: {atoms.get_chemical_formula()} ({len(atoms)} atoms)")
    print(f"   spacegroup {ds.international} ({ds.number}); "
          f"cell lengths {np.round(atoms.cell.lengths(), 4)}, "
          f"angles {np.round(atoms.cell.angles(), 2)}; V = {atoms.get_volume():.1f} A^3")
    mass_g = atoms.get_masses().sum() * 1.66053907e-24
    print(f"   density {mass_g / (atoms.get_volume() * 1e-24):.3f} g/cm3; "
          f"shortest contact {dist.min():.3f} A")
    pairs = {}
    for a, b, r in zip(i, j, dist):
        key = "-".join(sorted((sym[a], sym[b])))
        pairs.setdefault(key, []).append(r)
    for k in sorted(pairs):
        v = np.array(pairs[k])
        print(f"   {k:6s} n={len(v):4d}  min {v.min():.3f}  max {v.max():.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("structures"))
    OUT = ap.parse_args().out
    OUT.mkdir(parents=True, exist_ok=True)
    conv = build_conventional()
    prim = build_primitive(conv)
    for tag, atoms in (("conv_456", conv), ("prim_114", prim)):
        report(tag, atoms)
        write(OUT / f"uio66_{tag}.cif", atoms)
        write(OUT / f"uio66_{tag}.xyz", atoms, format="extxyz")
    print(f"\nwritten to {OUT}")


if __name__ == "__main__":
    main()
