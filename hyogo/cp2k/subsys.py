"""Write a CP2K &SUBSYS include (subsys.inc) from an extended xyz.

The input-writing part of make_subsys.py: the per-element valence and
potential come in as `kinds` (rookery's cp2k_kinds, read from the build's
own data folder), and X-H fixes happen before (yagura's
structure_stretch_xh), so this only turns a checked structure into text.
The output is make_subsys.py's, line for line, apart from the "written
by" comment.
"""
import re

import numpy as np


def read_extxyz(text: str):
    # deliberately a copy, not an import: hyogo meets yagura through tools
    lines = text.splitlines()
    n = int(lines[0])
    m = re.search(r'Lattice="([^"]+)"', lines[1])
    if not m:
        raise ValueError("line 2 has no Lattice=\"...\" (need extended xyz with the cell)")
    cell = np.array(m.group(1).split(), float).reshape(3, 3)
    el = [l.split()[0] for l in lines[2:2 + n]]
    xyz = np.array([[float(v) for v in l.split()[1:4]] for l in lines[2:2 + n]])
    return cell, el, xyz


def subsys(text: str, name: str, kinds: dict, periodic: str = "XYZ",
           charge: int = 0) -> tuple[str, dict]:
    """subsys.inc text for the structure `text` (file `name`), and a summary."""
    if periodic not in ("XYZ", "NONE"):
        raise ValueError('periodic is "XYZ" or "NONE"')
    cell, el, xyz = read_extxyz(text)
    elements = list(dict.fromkeys(el))
    missing = [e for e in elements if e not in kinds]
    if missing:
        raise ValueError(f"no kind for {missing}: pass cp2k_kinds' result for these elements "
                         "(an element in its `errors` has no such basis or potential)")
    for e in elements:
        k = kinds[e]
        if not (isinstance(k, dict) and isinstance(k.get("q"), int) and k.get("basis") and k.get("potential")):
            raise ValueError(f"kind for {e} needs q, basis and potential, as cp2k_kinds returns: {k!r}")
    nelec = sum(kinds[e]["q"] for e in el) - charge
    vol = abs(np.linalg.det(cell))
    widths = [vol / np.linalg.norm(np.cross(cell[(i + 1) % 3], cell[(i + 2) % 3])) for i in range(3)]
    warnings = []
    if periodic == "XYZ" and min(widths) < 10:
        warnings.append("a cell width is below 10 A; Gamma-point sampling may be poor, consider a supercell")
    if nelec % 2:
        warnings.append("odd electron count: the run input needs UKS .TRUE. and an even MULTIPLICITY")
    if periodic == "NONE":
        warnings.append("PERIODIC NONE: enable the &POISSON block in the run input (see cp2k.md)")

    formula = "".join(f"{e}{el.count(e)}" for e in elements)
    lines = [f"# {formula}, {len(el)} atoms, {nelec} valence electrons at charge {charge:+d}",
             f"# from {name}; written by cp2k_make_subsys",
             "&CELL"]
    lines += [f"  {n} {v[0]:14.8f} {v[1]:14.8f} {v[2]:14.8f}" for n, v in zip("ABC", cell)]
    lines += [f"  PERIODIC {periodic}", "&END CELL", "&COORD"]
    lines += [f"  {e:2s} {p[0]:14.8f} {p[1]:14.8f} {p[2]:14.8f}" for e, p in zip(el, xyz)]
    lines.append("&END COORD")
    if periodic == "NONE":
        lines += ["&TOPOLOGY", "  &CENTER_COORDINATES", "  &END CENTER_COORDINATES", "&END TOPOLOGY"]
    for e in elements:
        lines += [f"&KIND {e}", f"  BASIS_SET {kinds[e]['basis']}", f"  POTENTIAL {kinds[e]['potential']}",
                  "&END KIND"]
    info = {"formula": formula, "natoms": len(el), "electrons": nelec, "periodic": periodic,
            "widths": [round(float(w), 2) for w in widths],
            "kinds": {e: kinds[e]["potential"] for e in elements}, "warnings": warnings}
    return "\n".join(lines) + "\n", info
