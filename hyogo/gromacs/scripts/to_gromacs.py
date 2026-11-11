#!/usr/bin/env python3
"""tleap prmtop/inpcrd -> GROMACS top/gro with Li+ charge scaled by --scale (needs ParmEd)."""
import argparse
import parmed as pmd

ap = argparse.ArgumentParser()
ap.add_argument("--scale", type=float, required=True)
ap.add_argument("--box-nm-a", type=float, required=True, help="cubic box edge in Angstrom")
args = ap.parse_args()
s = pmd.load_file("system.prmtop", xyz="system.inpcrd")
for r in s.residues:
    if r.name == "LI":
        for a in r.atoms:
            a.charge *= args.scale
net = sum(a.charge for a in s.atoms)
print("atoms", len(s.atoms), "net charge", round(net, 6),
      "residues", {n: sum(1 for r in s.residues if r.name == n) for n in set(r.name for r in s.residues)})
for a in s.atoms:
    a.charge -= net / len(s.atoms)   # remove rounding residual
print("net charge after correction", sum(a.charge for a in s.atoms))
s.box = [args.box_nm_a] * 3 + [90, 90, 90]
s.save("system.top", format="gromacs", overwrite=True)
s.save("system.gro", overwrite=True)
