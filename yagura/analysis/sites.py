"""Atoms by environment, and per-site values summarised by it (G37).

Built for a slab under water (MXene + water labels) but geometric only:

  * nH: H atoms within h_cut (1.25 A) of the atom;
  * dM: distance to the nearest metal atom (0 for a metal);
  * class: "metal" for a metal; for O, "water_O" (nH == 2 and dM > water_min,
    3.5 A), "slab_O" (dM < slab_max, 2.5 A) or "other_O" (OH, H3O+, O near
    but not on the slab); any other atom is classed by its element.

Distances use the nearest periodic image (all 27 neighbouring images are
searched, so skewed cells are right). The thresholds and the metal list are
those of the MXene campaign's stray_moment.py and water_charge.py; its
label_spins.py's metal summary (sites below -0.5 uB, mean moment of those
above +0.5 uB) is in "metal_spin".

Numpy only; the extxyz reader is yagura/analysis/labels.py's.
"""
import numpy as np

from yagura.analysis.labels import read

# not metals: everything else in a structure is treated as one
NON_METALS = {"H", "He", "B", "C", "N", "O", "F", "Ne", "Si", "P", "S", "Cl", "Ar", "Ge", "As",
              "Se", "Br", "Kr", "Sb", "Te", "I", "Xe", "Rn"}
SHIFTS = np.array([[i, j, k] for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)], float)
SPIN = 0.5  # uB: label_spins.py's antiparallel / positive threshold
MOMENT_KEYS = ("REF_magmoms", "magmoms", "magmom", "magnetic_moments")


def _cell(info):
    if "Lattice" not in info:
        raise ValueError("the frame has no Lattice=\"...\": distances need the cell "
                         "(vasp_parse_output's sites_out writes it)")
    cell = np.array(info["Lattice"].split(), float).reshape(3, 3)
    if abs(np.linalg.det(cell)) < 1e-6:
        raise ValueError("the cell has zero volume")
    return cell


def _mindist(cell, frac, i, idx):
    """Nearest-image distances from atom i to atoms idx."""
    if not len(idx):
        return np.array([])
    d = frac[idx] - frac[i]
    d -= np.round(d)
    cart = (d[None, :, :] + SHIFTS[:, None, :]) @ cell
    return np.linalg.norm(cart, axis=2).min(axis=0)


def classify(symbols, pos, cell, metals=None, h_cut=1.25, water_min=3.5, slab_max=2.5):
    """Per atom: nH, dM (A), class."""
    metal = set(metals) if metals is not None else {s for s in symbols if s not in NON_METALS}
    frac = pos @ np.linalg.inv(cell)
    H = [i for i, s in enumerate(symbols) if s == "H"]
    M = [i for i, s in enumerate(symbols) if s in metal]
    out = []
    for i, s in enumerate(symbols):
        dh = _mindist(cell, frac, i, [j for j in H if j != i])
        nh = int((dh < h_cut).sum())
        dm = float(_mindist(cell, frac, i, M).min()) if M else float("inf")
        if s in metal:
            cls = "metal"
        elif s == "O":
            cls = "water_O" if nh == 2 and dm > water_min else "slab_O" if dm < slab_max else "other_O"
        else:
            cls = s
        out.append({"nH": nh, "dM": dm, "class": cls})
    return out, sorted(metal & set(symbols))


def summarise(text: str, name: str = "", frame: int = -1, metals=None, h_cut: float = 1.25,
              water_min: float = 3.5, slab_max: float = 2.5, rank_by: str | None = None,
              top: int = 5) -> tuple[dict, str]:
    """(result, a TSV with one row per atom)."""
    frames = read(text, name)
    try:
        f = frames[frame]
    except IndexError:
        raise ValueError(f"frame {frame}: the file has {len(frames)} frames") from None
    cell, symbols, pos = _cell(f["info"]), f["symbols"], f["pos"]
    sites, metal = classify(symbols, pos, cell, metals, h_cut, water_min, slab_max)
    cols = {k: v[:, 0] for k, v in f["arrays"].items() if v.shape[1] == 1}
    warnings = []
    if not metal:
        warnings.append("no metal atoms: every dM is infinite and no O is water_O or slab_O")
    classes = {}
    for i, s in enumerate(sites):
        classes.setdefault(s["class"], []).append(i)

    def stats(v):
        return {"mean": round(float(v.mean()), 4), "sum": round(float(v.sum()), 4),
                "sum_abs": round(float(np.abs(v).sum()), 4), "min": round(float(v.min()), 4),
                "max": round(float(v.max()), 4)}

    by_class = {c: {"count": len(idx), **{k: stats(v[idx]) for k, v in cols.items()}}
                for c, idx in sorted(classes.items())}
    mkey = next((k for k in MOMENT_KEYS if k in cols), None)
    rank = rank_by or mkey
    if rank is not None and rank not in cols:
        raise ValueError(f"rank_by {rank!r} is not a per-site column ({sorted(cols)})")
    largest = []
    if rank is not None:
        for i in np.argsort(-np.abs(cols[rank]), kind="stable")[:top]:
            largest.append({"atom": int(i) + 1, "element": symbols[i], rank: round(float(cols[rank][i]), 4),
                            "nH": sites[i]["nH"], "dM": round(sites[i]["dM"], 3), "class": sites[i]["class"]})
    metal_spin = None
    if mkey is not None and metal:
        def spin(idx):
            m = cols[mkey][idx]
            pos_m = m[m > SPIN]
            return {"n": len(idx), "net": round(float(m.sum()), 4), "antiparallel": int((m < -SPIN).sum()),
                    "mean_positive": round(float(pos_m.mean()), 4) if pos_m.size else 0.0}
        midx = classes.get("metal", [])
        metal_spin = {"all": spin(midx),
                      **{el: spin([i for i in midx if symbols[i] == el]) for el in metal}}
    else:
        warnings.append("no per-site moment column: no largest moments or metal spin summary")
    for c in ("water_O", "slab_O"):
        if "O" in symbols and metal and c not in classes:
            warnings.append(f"no {c} with these thresholds")
    info = {k: v for k, v in f["info"].items() if k not in ("Lattice", "Properties", "pbc")}
    result = {"natoms": len(symbols), "frame": f["index"], "frames_in_file": len(frames), "metals": metal,
              "cell_lengths": [round(float(x), 4) for x in np.linalg.norm(cell, axis=1)],
              "thresholds": {"h_cut": h_cut, "water_min": water_min, "slab_max": slab_max},
              "info": info, "columns": sorted(cols), "classes": by_class,
              "largest": largest, "metal_spin": metal_spin, "warnings": warnings}
    head = ["atom", "element", "class", "nH", "dM", *cols]
    lines = ["\t".join(head)]
    for i, s in enumerate(sites):
        lines.append("\t".join([str(i + 1), symbols[i], s["class"], str(s["nH"]), f"{s['dM']:.4f}",
                                *(f"{v[i]:.4f}" for v in cols.values())]))
    return result, "\n".join(lines) + "\n"
