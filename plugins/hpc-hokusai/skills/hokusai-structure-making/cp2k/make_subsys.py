#!/usr/bin/env python3
"""Write a CP2K &SUBSYS include (subsys.inc) from an extended xyz.

    /usr/bin/python3.12 -I make_subsys.py STRUCT.xyz --data-dir <cp2k>/data
        [--periodic XYZ|NONE] [--basis DZVP-MOLOPT-SR-GTH] [--stretch-ch 1.08]
        [--charge 0] [--out subsys.inc]

Needs only numpy, so it also runs on the HOKUSAI front end with the system
/usr/bin/python3.12. The run inputs include the result inside &SUBSYS with
`@INCLUDE subsys.inc`; see cp2k.md beside this script.

The xyz must be extended xyz with Lattice="ax ay az bx by bz cx cy cz" on line 2
(rows are the cell vectors), as written by ../scripts/to_extxyz.py or ASE. Each
element gets BASIS and the GTH-PBE pseudopotential with the same valence count q,
both looked up in the CP2K data folder, so q is never guessed (Zr q12, Ti q12,
Cu q11, N q5, ...).

--stretch-ch L moves every H bonded to C (< 1.2 Å) along the bond to length L
(1.08 for X-ray structures, whose C-H bonds come out near 0.95 Å).

read_extxyz, valence and stretch_ch are taken unchanged from the tested
make_cp2k_md_input.py (hokusai-cp2k-benchmark/scripts).
"""
import argparse
import re
import sys
from pathlib import Path

import numpy as np


def read_extxyz(path):
    lines = Path(path).read_text().splitlines()
    n = int(lines[0])
    m = re.search(r'Lattice="([^"]+)"', lines[1])
    if not m:
        sys.exit(f"{path}: line 2 has no Lattice=\"...\" (need extended xyz with the cell)")
    cell = np.array(m.group(1).split(), float).reshape(3, 3)
    el = [l.split()[0] for l in lines[2:2 + n]]
    xyz = np.array([[float(v) for v in l.split()[1:4]] for l in lines[2:2 + n]])
    return cell, el, xyz


def valence(data, element, basis):
    """q of BASIS for ELEMENT from BASIS_MOLOPT, checked against GTH_POTENTIALS."""
    q = None
    for line in (Path(data) / "BASIS_MOLOPT").read_text().splitlines():
        t = line.split()
        if len(t) >= 2 and t[0] == element and basis in t[1:]:
            for tok in t[1:]:
                mm = re.fullmatch(re.escape(basis) + r"-q(\d+)", tok)
                if mm:
                    q = int(mm.group(1))
            if q is None:
                sys.exit(f"{element} {basis}: found, but no -qN alias to read the valence from")
            break
    if q is None:
        sys.exit(f"no {basis} for {element} in {data}/BASIS_MOLOPT")
    pot = f"GTH-PBE-q{q}"
    if not any(l.split()[:2] == [element, pot] for l in (Path(data) / "GTH_POTENTIALS").read_text().splitlines()):
        sys.exit(f"no {element} {pot} in {data}/GTH_POTENTIALS")
    return q, pot


def stretch_ch(cell, el, xyz, length):
    inv = np.linalg.inv(cell)
    el = np.array(el)
    carbons = xyz[el == "C"]
    moved = 0
    if len(carbons) == 0:
        return xyz, 0
    for h in np.where(el == "H")[0]:
        d = (carbons - xyz[h]) @ inv
        d -= np.round(d)
        vec = d @ cell
        r = np.linalg.norm(vec, axis=1)
        k = r.argmin()
        if r[k] < 1.2:
            xyz[h] = xyz[h] - vec[k] / r[k] * (length - r[k])
            moved += 1
    return xyz, moved


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xyz")
    ap.add_argument("--data-dir", required=True,
                    help="the CP2K build's data folder, e.g. ~/software/cp2k/cp2k-2026.2/data")
    ap.add_argument("--periodic", choices=["XYZ", "NONE"], default="XYZ")
    ap.add_argument("--basis", default="DZVP-MOLOPT-SR-GTH")
    ap.add_argument("--charge", type=int, default=0, help="for the valence-electron count only")
    ap.add_argument("--stretch-ch", type=float, metavar="L")
    ap.add_argument("--out", default="subsys.inc")
    a = ap.parse_args()

    cell, el, xyz = read_extxyz(a.xyz)
    elements = list(dict.fromkeys(el))
    q = {e: valence(Path(a.data_dir).expanduser(), e, a.basis) for e in elements}
    nelec = sum(q[e][0] for e in el) - a.charge
    note = ""
    if a.stretch_ch:
        xyz, moved = stretch_ch(cell, el, xyz, a.stretch_ch)
        note = f"; {moved} C-H bonds set to {a.stretch_ch:.2f} A"
        print(f"moved {moved} H bonded to C to C-H = {a.stretch_ch} A")

    vol = abs(np.linalg.det(cell))
    widths = [vol / np.linalg.norm(np.cross(cell[(i + 1) % 3], cell[(i + 2) % 3])) for i in range(3)]
    print("cell perpendicular widths [A]:", " ".join(f"{w:.2f}" for w in widths))
    if a.periodic == "XYZ" and min(widths) < 10:
        print("WARNING: a cell width is below 10 A; Gamma-point sampling may be poor, consider a supercell")

    formula = "".join(f"{e}{el.count(e)}" for e in elements)
    lines = [f"# {formula}, {len(el)} atoms, {nelec} valence electrons at charge {a.charge:+d}",
             f"# from {Path(a.xyz).name}{note}; written by make_subsys.py",
             "&CELL"]
    lines += [f"  {n} {v[0]:14.8f} {v[1]:14.8f} {v[2]:14.8f}" for n, v in zip("ABC", cell)]
    lines += [f"  PERIODIC {a.periodic}", "&END CELL", "&COORD"]
    lines += [f"  {e:2s} {p[0]:14.8f} {p[1]:14.8f} {p[2]:14.8f}" for e, p in zip(el, xyz)]
    lines.append("&END COORD")
    if a.periodic == "NONE":
        lines += ["&TOPOLOGY", "  &CENTER_COORDINATES", "  &END CENTER_COORDINATES", "&END TOPOLOGY"]
    for e in elements:
        lines += [f"&KIND {e}", f"  BASIS_SET {a.basis}", f"  POTENTIAL {q[e][1]}", "&END KIND"]
    Path(a.out).write_text("\n".join(lines) + "\n")

    print(f"wrote {a.out}: {len(el)} atoms {formula}, {nelec} valence electrons, PERIODIC {a.periodic}")
    print("kinds: " + ", ".join(f"{e} {q[e][1]}" for e in elements))
    if nelec % 2:
        print("WARNING: odd electron count: the run input needs UKS .TRUE. and an even MULTIPLICITY")
    if a.periodic == "NONE":
        print("PERIODIC NONE: enable the &POISSON block in the run input (see cp2k.md)")


if __name__ == "__main__":
    main()
