"""Summarise a CIF before building: cell, space group, and every atom site with
its occupancy, flagging partial sites and which ones sum to 1 (alternative
positions) versus stay below 1 (vacancies / defects).

Run (needs ase, e.g. on your own machine): python inspect_cif.py file.cif
"""
import sys

from ase.io.cif import parse_cif


def num(x: str) -> float:
    return float(str(x).split("(")[0])


def main(path: str) -> None:
    b = next(iter(parse_cif(path)))
    cell = [num(b[f"_cell_length_{k}"]) for k in "abc"]
    ang = [num(b[f"_cell_angle_{k}"]) for k in ("alpha", "beta", "gamma")]
    print(f"cell {cell} angles {ang}")
    for key in ("_space_group_name_h-m_alt", "_symmetry_space_group_name_h-m",
                "_space_group_it_number", "_chemical_formula_sum",
                "_cell_formula_units_z"):
        if key in b:
            print(f"{key}: {b[key]}")
    labels = list(b["_atom_site_label"])
    types = list(b.get("_atom_site_type_symbol", labels))
    occ = [num(o) for o in b.get("_atom_site_occupancy", [1] * len(labels))]
    grp = list(b.get("_atom_site_disorder_group", ["."] * len(labels)))
    xyz = [[num(b[f"_atom_site_fract_{k}"][i]) for k in "xyz"] for i in range(len(labels))]
    print(f"\n{'label':8s}{'type':5s}{'x':>9s}{'y':>9s}{'z':>9s}{'occ':>7s}  group")
    for l, t, p, o, g in zip(labels, types, xyz, occ, grp):
        flag = "" if o >= 0.999 else "  <-- partial"
        print(f"{l:8s}{t:5s}{p[0]:9.4f}{p[1]:9.4f}{p[2]:9.4f}{o:7.2f}  {g}{flag}")
    partial = [l for l, o in zip(labels, occ) if o < 0.999]
    if partial:
        print(f"\npartial sites: {', '.join(partial)}")
        print("Atoms of one fragment at nearby positions whose occupancies sum to 1 "
              "are alternative positions; a common value below 1 means vacancies "
              "(missing linkers) with the gap filled by modulator or solvent.")

if __name__ == "__main__":
    main(sys.argv[1])
