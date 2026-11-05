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


# &CELL SYMMETRY names this tool can check: name -> (equal lengths, angle rule)
SYMMETRIES = {
    "CUBIC": ("abc", "all90"),
    "TETRAGONAL_AB": ("ab", "all90"),
    "TETRAGONAL_AC": ("ac", "all90"),
    "TETRAGONAL_BC": ("bc", "all90"),
    "ORTHORHOMBIC": ("", "all90"),
    "RHOMBOHEDRAL": ("abc", "allequal"),
}
LENGTH_TOL = 1e-3  # A
ANGLE_TOL = 0.01   # degrees


def cell_params(cell) -> tuple[list[float], list[float]]:
    """Lengths a, b, c (A) and angles alpha, beta, gamma (degrees)."""
    lengths = [float(np.linalg.norm(v)) for v in cell]
    angles = []
    for i, j in ((1, 2), (0, 2), (0, 1)):
        c = np.dot(cell[i], cell[j]) / (lengths[i] * lengths[j])
        angles.append(float(np.degrees(np.arccos(np.clip(c, -1, 1)))))
    return lengths, angles


def check_symmetry(cell, symmetry: str) -> None:
    """Refuse a SYMMETRY the cell vectors don't have (CP2K would impose it)."""
    if symmetry not in SYMMETRIES:
        raise ValueError(f"symmetry is one of {list(SYMMETRIES)}; other CP2K lattices are not "
                         "checked by this tool")
    equal, rule = SYMMETRIES[symmetry]
    lengths, angles = cell_params(cell)
    idx = {"a": 0, "b": 1, "c": 2}
    same = [lengths[idx[x]] for x in equal]
    ok = all(abs(x - same[0]) <= LENGTH_TOL for x in same)
    if rule == "all90":
        ok = ok and all(abs(t - 90) <= ANGLE_TOL for t in angles)
    else:
        ok = ok and all(abs(t - angles[0]) <= ANGLE_TOL for t in angles)
    if not ok:
        raise ValueError(f"the cell is not {symmetry}: a, b, c = "
                         f"{', '.join(f'{x:.4f}' for x in lengths)} A, alpha, beta, gamma = "
                         f"{', '.join(f'{t:.3f}' for t in angles)} deg")


def subsys(text: str, name: str, kinds: dict, periodic: str = "XYZ",
           charge: int = 0, symmetry: str | None = None) -> tuple[str, dict]:
    """subsys.inc text for the structure `text` (file `name`), and a summary.

    symmetry writes &CELL SYMMETRY (needed by CELL_OPT KEEP_SYMMETRY) after
    checking the cell has it."""
    if periodic not in ("XYZ", "NONE"):
        raise ValueError('periodic is "XYZ" or "NONE"')
    cell, el, xyz = read_extxyz(text)
    if symmetry is not None:
        if periodic == "NONE":
            raise ValueError("symmetry is for a periodic cell; drop it with periodic=\"NONE\"")
        check_symmetry(cell, symmetry)
    elements = list(dict.fromkeys(el))
    missing = [e for e in elements if e not in kinds]
    if missing:
        raise ValueError(f"no kind for {missing}: pass cp2k_kinds' result for these elements "
                         "(an element in its `errors` has no such basis or potential)")
    for e in elements:
        k = kinds[e]
        if not (isinstance(k, dict) and isinstance(k.get("q"), int) and k.get("basis") and k.get("potential")):
            raise ValueError(f"kind for {e} needs q, basis and potential, as cp2k_kinds returns: {k!r}")
        if "aux_basis" in k and not (isinstance(k["aux_basis"], str) and k["aux_basis"]):
            raise ValueError(f"aux_basis for {e} is a basis name, as cp2k_kinds returns: {k!r}")
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
    lines.append(f"  PERIODIC {periodic}")
    if symmetry is not None:
        lines.append(f"  SYMMETRY {symmetry}")
    lines += ["&END CELL", "&COORD"]
    lines += [f"  {e:2s} {p[0]:14.8f} {p[1]:14.8f} {p[2]:14.8f}" for e, p in zip(el, xyz)]
    lines.append("&END COORD")
    if periodic == "NONE":
        lines += ["&TOPOLOGY", "  &CENTER_COORDINATES", "  &END CENTER_COORDINATES", "&END TOPOLOGY"]
    for e in elements:
        lines += [f"&KIND {e}", f"  BASIS_SET {kinds[e]['basis']}", f"  POTENTIAL {kinds[e]['potential']}"]
        if kinds[e].get("aux_basis"):
            lines.append(f"  BASIS_SET AUX_FIT {kinds[e]['aux_basis']}")
        lines.append("&END KIND")
    info = {"formula": formula, "natoms": len(el), "electrons": nelec, "charge": charge,
            "periodic": periodic, "symmetry": symmetry,
            "widths": [round(float(w), 2) for w in widths],
            "kinds": {e: kinds[e]["potential"] for e in elements},
            "aux_basis": {e: kinds[e]["aux_basis"] for e in elements if kinds[e].get("aux_basis")},
            "warnings": warnings}
    return "\n".join(lines) + "\n", info
