"""Set X-H bond lengths in an extended xyz (X-ray H positions come out short).

X-ray structures place H at the electron density, so C-H bonds come out
near 0.95 A instead of ~1.09 A. Each H whose nearest atom of an element X
(for the X-H pairs asked) is within 1.2 A (minimum image) is moved along
that bond to the target length. Everything else in the file is kept: line 2,
the other atoms, extra columns.
"""
import re

import numpy as np

BONDED = 1.2   # an H closer than this to X counts as bonded to it (A)


def stretch(text: str, length: float = 1.08, pairs: tuple[str, ...] = ("C-H",)) -> tuple[str, dict]:
    """The file text with X-H bonds set to `length`, and what moved."""
    lines = text.splitlines()
    n = int(lines[0])
    m = re.search(r'Lattice="([^"]+)"', lines[1])
    if not m:
        raise ValueError("line 2 has no Lattice=\"...\" (need extended xyz)")
    heavy = []
    for p in pairs:
        x, _, h = p.partition("-")
        if h != "H" or not x:
            raise ValueError(f"pairs are X-H, e.g. C-H or N-H; got {p!r}")
        heavy.append(x)
    if not 0.8 <= length <= 1.3:
        raise ValueError("length is an X-H bond in A, between 0.8 and 1.3")
    cell = np.array(m.group(1).split(), float).reshape(3, 3)
    inv = np.linalg.inv(cell)
    rows = [l.split() for l in lines[2:2 + n]]
    el = np.array([r[0] for r in rows])
    xyz = np.array([[float(v) for v in r[1:4]] for r in rows])
    anchors = np.where(np.isin(el, heavy))[0]
    moved, before = [], []
    if len(anchors):
        for h in np.where(el == "H")[0]:
            d = (xyz[anchors] - xyz[h]) @ inv
            d -= np.round(d)
            vec = d @ cell
            r = np.linalg.norm(vec, axis=1)
            k = r.argmin()
            if r[k] < BONDED:
                before.append(float(r[k]))
                xyz[h] = xyz[h] - vec[k] / r[k] * (length - r[k])
                moved.append(int(h))
    for i in moved:
        rows[i][1:4] = [f"{v:.8f}" for v in xyz[i]]
    out = lines[:2] + [" ".join(r) for r in rows] + lines[2 + n:]
    info = {"moved": len(moved), "pairs": list(pairs), "length": length,
            "shortest_before": round(min(before), 3) if before else None}
    return "\n".join(out) + "\n", info
