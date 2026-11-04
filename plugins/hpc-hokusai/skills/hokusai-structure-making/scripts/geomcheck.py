"""Sanity-check a periodic structure (extended xyz) before it becomes a benchmark input.

Usage (light, fine on the front end):
    /usr/bin/python3.12 -I geomcheck.py STRUCT.xyz [--json]
Prints composition, cell lengths/angles/volume/perpendicular widths, the shortest
distance for every element pair (minimum image, pairs below 3.2 A) and flags:
  - any distance below 0.7 A (overlapping or duplicated atoms, typical of disorder
    left in from a CIF with partial occupancies);
  - C-H or N-H below 1.0 A (X-ray H positions; stretch them, e.g. with
    make_cp2k_md_input.py --stretch-ch 1.08).
--json prints the same as one JSON object (what yagura's structure_check returns).
Exit status 1 when anything is flagged.
"""
import itertools
import json
import re
import sys
from pathlib import Path

import numpy as np

PAIR_CUTOFF = 3.2   # report pairs closer than this (A)
OVERLAP = 0.7       # closer than this: overlapping or duplicated atoms
SHORT_XH = 1.0      # C-H / N-H closer than this: X-ray H positions


def check(text: str) -> dict:
    """The checks above on the text of an extended xyz file."""
    lines = text.splitlines()
    n = int(lines[0])
    m = re.search(r'Lattice="([^"]+)"', lines[1])
    if not m:
        raise ValueError("line 2 has no Lattice=\"...\" (need extended xyz)")
    A = np.array(m.group(1).split(), float).reshape(3, 3)
    el = np.array([l.split()[0] for l in lines[2:2 + n]])
    X = np.array([[float(v) for v in l.split()[1:4]] for l in lines[2:2 + n]])

    elements = list(dict.fromkeys(el))
    L = np.linalg.norm(A, axis=1)
    ang = [np.degrees(np.arccos(A[j] @ A[k] / (L[j] * L[k]))) for j, k in [(1, 2), (0, 2), (0, 1)]]
    vol = abs(np.linalg.det(A))
    widths = [vol / np.linalg.norm(np.cross(A[(i + 1) % 3], A[(i + 2) % 3])) for i in range(3)]

    F = X @ np.linalg.inv(A)
    D = F[:, None, :] - F[None, :, :]
    D -= np.round(D)
    D = np.linalg.norm(D @ A, axis=2)
    np.fill_diagonal(D, np.inf)
    pairs, flags = [], []
    for a, b in itertools.combinations_with_replacement(elements, 2):
        sub = D[np.ix_(el == a, el == b)]
        dmin = float(sub.min())
        if dmin < PAIR_CUTOFF:
            cnt = int((sub < dmin + 0.05).sum()) // (2 if a == b else 1)
            pairs.append({"pair": f"{a}-{b}", "d": round(dmin, 3), "count": cnt})
            if dmin < OVERLAP:
                flags.append({"pair": f"{a}-{b}", "d": round(dmin, 3), "kind": "overlap"})
            elif {a, b} in ({"C", "H"}, {"N", "H"}) and dmin < SHORT_XH:
                flags.append({"pair": f"{a}-{b}", "d": round(dmin, 3), "kind": "short_xh"})
    return {"natoms": n,
            "composition": {str(e): int((el == e).sum()) for e in elements},
            "cell": {"lengths": [round(float(x), 4) for x in L],
                     "angles": [round(float(x), 3) for x in ang],
                     "volume": round(float(vol), 2),
                     "widths": [round(float(w), 2) for w in widths]},
            "min_pair_distances": pairs, "flags": flags, "ok": not flags}


def report(r: dict) -> str:
    flagged = {f["pair"]: f["kind"] for f in r["flags"]}
    note = {"overlap": "  <-- overlap/duplicate atoms",
            "short_xh": "  <-- short X-H (X-ray H positions?)"}
    out = ["atoms " + str(r["natoms"]) + " " + " ".join(f"{e}{c}" for e, c in r["composition"].items()),
           "a b c    " + " ".join(f"{x:.4f}" for x in r["cell"]["lengths"]),
           "angles   " + " ".join(f"{x:.3f}" for x in r["cell"]["angles"]),
           f"volume   {r['cell']['volume']:.2f} A^3; perpendicular widths "
           + " ".join(f"{w:.2f}" for w in r["cell"]["widths"]),
           f"shortest distance per pair (< {PAIR_CUTOFF} A):"]
    for p in r["min_pair_distances"]:
        a, b = p["pair"].split("-")
        out.append(f"  {a:2s}-{b:2s} {p['d']:6.3f}  (x{p['count']}){note.get(flagged.get(p['pair']), '')}")
    out.append("OK" if r["ok"] else f"{len(r['flags'])} issue(s) flagged")
    return "\n".join(out)


def main(argv):
    as_json = "--json" in argv
    paths = [a for a in argv if a != "--json"]
    if len(paths) != 1:
        sys.exit("usage: geomcheck.py STRUCT.xyz [--json]")
    try:
        r = check(Path(paths[0]).read_text())
    except ValueError as e:
        sys.exit(str(e))
    print(json.dumps(r) if as_json else report(r))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
