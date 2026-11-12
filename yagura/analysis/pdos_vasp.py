"""Read a VASP vasprun.xml(.gz): the DOS VASP wrote (total and, with
LORBIT >= 10, per ion and channel), E_F, the band edges from the
eigenvalues, and the final structure; then sum the projections over groups
of atoms (by element, by environment as in sites.py, or named lists) and
say how many states each group holds near E_F (G37).

Streamed with iterparse, so the <projected> block (per band and k-point,
most of a big file) is skipped without being held. The last <dos> and the
last <calculation>'s eigenvalues are read (a relaxation or MD prints one
per ionic step). Numpy only.

The numbers near E_F follow the MXene campaign's water_pdos.py: per atom
of the group, the PDOS summed over grid points in (E_F - w, E_F] and
(E_F, E_F + w] times the grid spacing, and the "edge": the highest energy
at or below E_F + w where the group's PDOS per atom exceeds a threshold
(0.05 states/eV).
"""
import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from yagura.analysis import sites as sites_mod

SPINS = {1: "alpha", 2: "beta"}


def is_vasprun(path: Path) -> bool:
    return path.name.endswith((".xml", ".xml.gz")) and "vasprun" in path.name


def _open(path: Path):
    with open(path, "rb") as fh:
        gz = fh.read(2) == b"\x1f\x8b"
    return gzip.open(path, "rb") if gz else open(path, "rb")


def _rows(el, tag="r"):
    return [[float(x) for x in r.text.split()] for r in el.iter(tag)]


def parse(path: Path) -> dict:
    """{symbols, cell, frac, e_fermi, energy, total {spin: dos}, channels,
    partial {spin: (ion x energy x channel)} or None, eig {spin: [(e, occ)]},
    calculations, incar}."""
    symbols, cell, frac, efermi = [], None, None, None
    energy, total, partial, channels = None, {}, None, []
    eig, ncalc, incar = {}, 0, {}
    stack = []
    with _open(path) as fh:
        for ev, el in ET.iterparse(fh, events=("start", "end")):
            if ev == "start":
                stack.append(el.tag)
                continue
            tag, parent = el.tag, stack[-2] if len(stack) > 1 else None
            stack.pop()
            if tag == "i" and parent == "incar":
                incar[el.get("name")] = (el.text or "").strip()
            elif tag == "array" and el.get("name") == "atoms":
                symbols = [r[0].text.strip() for r in el.find("set")]
                el.clear()
            elif tag == "structure" and el.get("name") == "finalpos":
                cell = np.array(_rows(el.find("crystal/varray[@name='basis']"), "v"))
                frac = np.array(_rows(el.find("varray[@name='positions']"), "v"))
                el.clear()
            elif tag == "calculation":
                ncalc += 1
                el.clear()
            elif tag == "eigenvalues" and parent == "calculation":
                eig = {}
                for s in el.find("array/set"):
                    k = int(s.get("comment").split()[-1])
                    eig[k] = np.concatenate([np.array(_rows(kp)) for kp in s])
                el.clear()
            elif tag == "projected" or (tag == "set" and "projected" in stack):
                el.clear()                            # free each band's rows as they pass
            elif tag == "dos":
                ef = el.find("i[@name='efermi']")
                efermi = float(ef.text) if ef is not None else None
                total, partial = {}, None
                for s in el.find("total/array/set"):
                    a = np.array(_rows(s))
                    energy = a[:, 0]
                    total[int(s.get("comment").split()[-1])] = a[:, 1]
                p = el.find("partial/array")
                if p is not None:
                    channels = [f.text.strip() for f in p.findall("field")][1:]
                    partial = {}
                    for ion in p.find("set"):
                        for s in ion:
                            k = int(s.get("comment").split()[-1])
                            partial.setdefault(k, []).append(np.array(_rows(s))[:, 1:])
                    partial = {k: np.array(v) for k, v in partial.items()}
                el.clear()
    if energy is None:
        raise ValueError(f"{path.name}: no <dos> block (an unfinished run?)")
    if efermi is None:
        raise ValueError(f"{path.name}: no efermi in the <dos> block")
    return {"symbols": symbols, "cell": cell, "frac": frac, "e_fermi": efermi, "energy": energy,
            "total": total, "channels": channels, "partial": partial, "eig": eig,
            "calculations": ncalc, "incar": incar}


def _l(ch: str) -> str:
    """'px' -> 'p', 'dxy' -> 'd', 'x2-y2' -> 'd', 'fy3x2' -> 'f'."""
    return ch[0] if ch[0] in "spdf" else "d" if ch in ("x2-y2", "dx2") else ch


def analyse(path: Path, window_ev: float = 0.5, emin_ev: float = -6.0, emax_ev: float = 8.0,
            group_by: str = "element", groups: dict | None = None, environment: dict | None = None,
            edge_threshold: float = 0.05) -> tuple[str, dict]:
    """(the DOS table, E - E_F in eV; the summary)."""
    if window_ev <= 0 or emax_ev <= emin_ev or edge_threshold <= 0:
        raise ValueError("window_ev and edge_threshold are positive and emin_ev < emax_ev")
    if group_by not in ("element", "environment"):
        raise ValueError("group_by is 'element' or 'environment'")
    d = parse(path)
    sym, n = d["symbols"], len(d["symbols"])
    warnings = []
    e = d["energy"] - d["e_fermi"]
    de = float(e[1] - e[0])
    if window_ev / de < 5:
        warnings.append(f"the DOS grid is {de * 1000:.0f} meV, so (E_F - {window_ev}, E_F] holds "
                        f"{int(round(window_ev / de))} point(s): the numbers near E_F are coarse. "
                        "Raise NEDOS (or narrow EMIN/EMAX) and rerun non-self-consistently")
    if d["calculations"] > 1:
        warnings.append(f"{d['calculations']} ionic steps: the last step's DOS and structure are read")

    # groups of atoms (0-based)
    sel = {}
    env_info = None
    if d["partial"] is not None:
        if group_by == "element":
            for i, s in enumerate(sym):
                sel.setdefault(s, []).append(i)
        else:
            if d["cell"] is None:
                raise ValueError("no final structure in the file: group_by='environment' needs it")
            env = dict(environment or {})
            bad = set(env) - {"metals", "h_cut", "water_min", "slab_max"}
            if bad:
                raise ValueError(f"environment takes metals, h_cut, water_min, slab_max; not {sorted(bad)}")
            sites, metals = sites_mod.classify(sym, d["frac"] @ d["cell"], d["cell"], **env)
            for i, s in enumerate(sites):
                sel.setdefault(s["class"], []).append(i)
            env_info = {"metals": metals, **{k: env.get(k, v) for k, v in
                                             (("h_cut", 1.25), ("water_min", 3.5), ("slab_max", 2.5))}}
            if not metals:
                warnings.append("no metal atoms: no O is water_O or slab_O")
        for name, atoms in (groups or {}).items():
            idx = [int(a) - 1 for a in atoms]
            if not idx or min(idx) < 0 or max(idx) >= n:
                raise ValueError(f"group {name!r}: atom numbers run 1..{n}")
            if name in sel:
                raise ValueError(f"group {name!r} clashes with a {group_by} group")
            sel[name] = idx
        sel = dict(sorted(sel.items()))
    else:
        warnings.append("no projected DOS (<partial>): LORBIT >= 10 writes it; total DOS only")
        if groups:
            raise ValueError("groups need the projected DOS: rerun with LORBIT >= 10")

    below = (e > -window_ev) & (e <= 0)
    above = (e > 0) & (e <= window_ev)
    spins_out, cols, names = {}, [], []
    for k in sorted(d["total"]):
        spin = SPINS[k] if len(d["total"]) == 2 else "none"
        tag = "" if spin == "none" else f"{spin}:"
        tot = d["total"][k]
        out = {"dos_at_ef": round(float(np.interp(0.0, e, tot)), 4)}
        if k in d["eig"]:
            ev, occ = d["eig"][k][:, 0], d["eig"][k][:, 1]
            occd = occ > 0.5
            homo, lumo = float(ev[occd].max()), float(ev[~occd].min()) if (~occd).any() else None
            out.update({"homo_ev": round(homo, 4), "lumo_ev": None if lumo is None else round(lumo, 4),
                        "partly_occupied_states": int(((occ > 0.01) & (occ < 0.99)).sum())})
        names.append(f"{tag}total")
        cols.append(tot)
        gout = {}
        for g, idx in sel.items():
            pd = d["partial"][k][idx]                 # atom x energy x channel
            per_atom = pd.sum(axis=0) / len(idx)      # energy x channel
            dos = per_atom.sum(axis=1)
            hit = np.where((dos > edge_threshold) & (e <= window_ev))[0]
            chans = {}
            for j, ch in enumerate(d["channels"]):
                key = _l(ch)
                chans[key] = chans.get(key, 0.0) + float(per_atom[below | above, j].sum() * de)
            gout[g] = {"atoms": len(idx),
                       "below_ef": round(float(dos[below].sum() * de), 6),
                       "above_ef": round(float(dos[above].sum() * de), 6),
                       "channels_near_ef": {c: round(v, 6) for c, v in chans.items()},
                       "edge_ev": round(float(e[hit.max()]), 4) if hit.size else None}
            names.append(f"{tag}{g}")
            cols.append(pd.sum(axis=(0, 2)))
        if gout:
            out["groups"] = gout
        spins_out[spin] = out

    homos = [v["homo_ev"] for v in spins_out.values() if "homo_ev" in v]
    lumos = [v["lumo_ev"] for v in spins_out.values() if v.get("lumo_ev") is not None]
    gap = None
    partly = sum(v.get("partly_occupied_states", 0) for v in spins_out.values())
    if partly:
        warnings.append(f"{partly} states partly occupied (0.01-0.99) across E_F: metallic, or SIGMA "
                        "too large to show a gap; gap_ev is None")
    elif homos and len(lumos) == len(homos):
        gap = round(max(0.0, min(lumos) - max(homos)), 4)

    keep = (e >= emin_ev) & (e <= emax_ev)
    head = [f"# VASP DOS as written (VASP's own smearing, not re-broadened), E - E_F in eV "
            f"(E_F {d['e_fermi']:.4f} eV), states/eV; a group's column is its sum over atoms",
            "# E " + " ".join(names)]
    allc = np.array(cols).T
    rows = [f"{E:9.4f} " + " ".join(f"{v:12.5f}" for v in allc[i]) for i, E in zip(np.where(keep)[0], e[keep])]
    result = {"files": [path.name], "code": "vasp", "natoms": n, "e_fermi_ev": round(d["e_fermi"], 4),
              "grid_mev": round(de * 1000, 1), "channels": d["channels"], "group_by": group_by,
              "environment": env_info, "spins": spins_out, "gap_ev": gap, "window_ev": window_ev,
              "edge_threshold": edge_threshold, "warnings": warnings}
    return "\n".join(head + rows) + "\n", result
