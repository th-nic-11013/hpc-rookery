"""Summarise a CIF before building: cell, space group, and every atom site with
its occupancy, flagging partial sites and which ones sum to 1 (alternative
positions) versus stay below 1 (vacancies / defects).

Run (needs ase, e.g. on your own machine): python inspect_cif.py file.cif
yagura's cif_inspect returns the same as a dict (inspect()).
"""
import sys

from ase.io.cif import parse_cif

SPACE_GROUP_KEYS = ("_space_group_name_h-m_alt", "_symmetry_space_group_name_h-m",
                    "_space_group_it_number", "_chemical_formula_sum",
                    "_cell_formula_units_z")


def num(x: str) -> float:
    return float(str(x).split("(")[0])


def inspect(path: str) -> dict:
    """The first data block of a CIF: cell, symmetry keys and every atom site."""
    b = next(iter(parse_cif(path)))
    labels = list(b["_atom_site_label"])
    types = list(b.get("_atom_site_type_symbol", labels))
    occ = [num(o) for o in b.get("_atom_site_occupancy", [1] * len(labels))]
    grp = [str(g) for g in b.get("_atom_site_disorder_group", ["."] * len(labels))]
    xyz = [[num(b[f"_atom_site_fract_{k}"][i]) for k in "xyz"] for i in range(len(labels))]
    sites = [{"label": l, "element": t, "x": p[0], "y": p[1], "z": p[2], "occupancy": o, "group": g}
             for l, t, p, o, g in zip(labels, types, xyz, occ, grp)]
    partial = [s["label"] for s in sites if s["occupancy"] < 0.999]
    groups: dict[str, list[str]] = {}
    for s in sites:
        if s["group"] not in (".", "?"):
            groups.setdefault(s["group"], []).append(s["label"])
    return {"cell": {"lengths": [num(b[f"_cell_length_{k}"]) for k in "abc"],
                     "angles": [num(b[f"_cell_angle_{k}"]) for k in ("alpha", "beta", "gamma")]},
            "keys": {k: str(b[k]) for k in SPACE_GROUP_KEYS if k in b},
            "sites": sites, "partial": partial, "partial_groups": groups}


def report(r: dict) -> str:
    out = [f"cell {r['cell']['lengths']} angles {r['cell']['angles']}"]
    out += [f"{k}: {v}" for k, v in r["keys"].items()]
    out.append(f"\n{'label':8s}{'type':5s}{'x':>9s}{'y':>9s}{'z':>9s}{'occ':>7s}  group")
    for s in r["sites"]:
        flag = "" if s["occupancy"] >= 0.999 else "  <-- partial"
        out.append(f"{s['label']:8s}{s['element']:5s}{s['x']:9.4f}{s['y']:9.4f}{s['z']:9.4f}"
                   f"{s['occupancy']:7.2f}  {s['group']}{flag}")
    if r["partial"]:
        out.append(f"\npartial sites: {', '.join(r['partial'])}")
        out.append("Atoms of one fragment at nearby positions whose occupancies sum to 1 "
                   "are alternative positions; a common value below 1 means vacancies "
                   "(missing linkers) with the gap filled by modulator or solvent.")
    return "\n".join(out)


def main(path: str) -> None:
    print(report(inspect(path)))


if __name__ == "__main__":
    main(sys.argv[1])
