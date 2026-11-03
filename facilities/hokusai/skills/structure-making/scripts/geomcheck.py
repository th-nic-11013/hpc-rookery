"""Sanity-check a periodic structure (extended xyz) before it becomes a benchmark input.

Usage (light, fine on the front end):
    /usr/bin/python3.12 -I geomcheck.py STRUCT.xyz
Prints composition, cell lengths/angles/volume/perpendicular widths, the shortest
distance for every element pair (minimum image, pairs below 3.2 A) and flags:
  - any distance below 0.7 A (overlapping or duplicated atoms, typical of disorder
    left in from a CIF with partial occupancies);
  - C-H or N-H below 1.0 A (X-ray H positions; stretch them, e.g. with
    make_cp2k_md_input.py --stretch-ch 1.08).
"""
import itertools
import re
import sys
from pathlib import Path

import numpy as np


def main(path):
    lines = Path(path).read_text().splitlines()
    n = int(lines[0])
    m = re.search(r'Lattice="([^"]+)"', lines[1])
    if not m:
        sys.exit("line 2 has no Lattice=\"...\" (need extended xyz)")
    A = np.array(m.group(1).split(), float).reshape(3, 3)
    el = np.array([l.split()[0] for l in lines[2:2 + n]])
    X = np.array([[float(v) for v in l.split()[1:4]] for l in lines[2:2 + n]])

    elements = list(dict.fromkeys(el))
    print("atoms", n, " ".join(f"{e}{(el == e).sum()}" for e in elements))
    L = np.linalg.norm(A, axis=1)
    ang = [np.degrees(np.arccos(A[j] @ A[k] / (L[j] * L[k]))) for j, k in [(1, 2), (0, 2), (0, 1)]]
    vol = abs(np.linalg.det(A))
    widths = [vol / np.linalg.norm(np.cross(A[(i + 1) % 3], A[(i + 2) % 3])) for i in range(3)]
    print("a b c   ", " ".join(f"{x:.4f}" for x in L))
    print("angles  ", " ".join(f"{x:.3f}" for x in ang))
    print(f"volume   {vol:.2f} A^3; perpendicular widths " + " ".join(f"{w:.2f}" for w in widths))

    F = X @ np.linalg.inv(A)
    D = F[:, None, :] - F[None, :, :]
    D -= np.round(D)
    D = np.linalg.norm(D @ A, axis=2)
    np.fill_diagonal(D, np.inf)
    problems = 0
    print("shortest distance per pair (< 3.2 A):")
    for a, b in itertools.combinations_with_replacement(elements, 2):
        sub = D[np.ix_(el == a, el == b)]
        dmin = sub.min()
        if dmin < 3.2:
            cnt = int((sub < dmin + 0.05).sum()) // (2 if a == b else 1)
            flag = ""
            if dmin < 0.7:
                flag, problems = "  <-- overlap/duplicate atoms", problems + 1
            elif {a, b} in ({"C", "H"}, {"N", "H"}) and dmin < 1.0:
                flag, problems = "  <-- short X-H (X-ray H positions?)", problems + 1
            print(f"  {a:2s}-{b:2s} {dmin:6.3f}  (x{cnt}){flag}")
    print("OK" if problems == 0 else f"{problems} issue(s) flagged")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
