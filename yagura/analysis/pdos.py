"""Read CP2K projected density of states (.pdos) files: band edges, the
gap they imply, which atomic kinds and angular-momentum channels the
edges sit on, and a broadened DOS to plot. Numpy only.

One file per atomic kind (`<PROJECT>-k<n>-1.pdos`, or `-ALPHA_k<n>-` /
`-BETA_k<n>-` with spin); each holds every MO with its eigenvalue
(hartree), occupation and the projection of that MO onto the kind, per
channel (s p d f) or per component (with COMPONENTS). Two header forms
are read:

  CP2K 2026.x  "# Projected DOS for atomic kind Zr at iteration step i = 0"
               "# E(Fermi) = ... a.u. = ... eV", "# E(HOCO) = ...",
               then "# MO Energy[a.u.] Occupation Total s p d ..."
  older        "# Projected DOS for atomic kind Zr at iteration step i = 0, E(Fermi) = ... a.u."
               then "# MO Eigenvalue [a.u.] Occupation s p d ..."

With APPEND the file holds one block per print; the last is read. The
projections over all kinds sum to ~1 per MO (Lowdin), which is how a
missing kind file is caught.
"""
import math
import re
from pathlib import Path

import numpy as np

HARTREE_EV = 27.211386245988
OCC_TOL = 1e-3   # an MO with occupation above this is occupied


def _element(kind: str) -> str:
    m = re.match(r"[A-Z][a-z]?", kind)
    return m.group(0) if m else kind


def parse(text: str, name: str = "") -> dict:
    """One .pdos file -> {kind, element, step, e_fermi_ev, channels,
    energy_ev, occupation, weights (MO x channel), total, blocks}."""
    blocks, cur = [], None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("# Projected DOS"):
            cur = {"head": s, "meta": [], "cols": None, "rows": []}
            blocks.append(cur)
        elif cur is None:
            raise ValueError(f"{name}: not a CP2K .pdos file (no '# Projected DOS' header)")
        elif s.startswith("#"):
            if "Occupation" in s:
                cur["cols"] = s.split("Occupation", 1)[1].split()
            else:
                cur["meta"].append(s)
        else:
            cur["rows"].append(s.split())
    if not blocks:
        raise ValueError(f"{name}: not a CP2K .pdos file (no '# Projected DOS' header)")
    b = blocks[-1]
    m = re.search(r"atomic kind (\S+?),?\s+at iteration step i =\s*(\d+)", b["head"])
    lst = re.search(r"for list (\d+)", b["head"])
    if not m and not lst:
        raise ValueError(f"{name}: unreadable header {b['head']!r}")
    if b["cols"] is None or not b["rows"]:
        raise ValueError(f"{name}: header without MO rows (a truncated file?)")
    kind = m.group(1) if m else f"list{lst.group(1)}"
    step = int(m.group(2)) if m else None
    ef = None
    for s in [b["head"]] + b["meta"]:
        f = re.search(r"E\(Fermi\)\s*=\s*(-?[\d.]+(?:[eE][-+]?\d+)?)\s*a\.u\.", s)
        if f:
            ef = float(f.group(1)) * HARTREE_EV
    try:
        a = np.array(b["rows"], dtype=float)
    except ValueError as e:
        raise ValueError(f"{name}: non-numeric MO row ({e})") from e
    cols = list(b["cols"])
    if a.shape[1] != 3 + len(cols):
        raise ValueError(f"{name}: {a.shape[1]} columns but the header names {3 + len(cols)}")
    has_total = cols[:1] == ["Total"]
    channels = cols[1:] if has_total else cols
    w = a[:, 4:] if has_total else a[:, 3:]
    total = a[:, 3] if has_total else w.sum(axis=1)
    return {"kind": kind, "element": _element(kind), "step": step, "e_fermi_ev": ef,
            "channels": channels, "energy_ev": a[:, 1] * HARTREE_EV, "occupation": a[:, 2],
            "weights": w, "total": total, "blocks": len(blocks)}


def _spin(path: Path) -> str:
    n = path.name
    return "alpha" if "-ALPHA_" in n else "beta" if "-BETA_" in n else "none"


def _channel_l(ch: str) -> str:
    """'px' -> 'p', 'd-2' -> 'd', 's' -> 's'."""
    return ch[0] if ch and ch[0] in "spdfgh" else ch


def analyse(paths: list[Path], window_ev: float = 0.5, sigma_ev: float = 0.1,
            de_ev: float = 0.01, emin_ev: float = -6.0, emax_ev: float = 8.0) -> tuple[str, dict]:
    """The DOS table (text, energies relative to the HOMO) and the summary."""
    if window_ev <= 0 or sigma_ev <= 0 or de_ev <= 0 or emax_ev <= emin_ev:
        raise ValueError("window_ev, sigma_ev and de_ev are positive and emin_ev < emax_ev")
    files = [(p, parse(p.read_text(), p.name)) for p in paths]
    warnings = []
    for p, f in files:
        if f["blocks"] > 1:
            warnings.append(f"{p.name}: {f['blocks']} blocks (APPEND): read the last")

    spins = {}
    for p, f in files:
        spins.setdefault(_spin(p), []).append((p, f))
    if "none" in spins and len(spins) > 1:
        raise ValueError("files from a spin-polarised run (ALPHA/BETA) and a closed-shell one mixed")

    out_spins, tables = {}, []
    for spin, fs in sorted(spins.items()):
        kinds = [f["kind"] for _, f in fs]
        if len(set(kinds)) != len(kinds):
            raise ValueError(f"kind {sorted(k for k in kinds if kinds.count(k) > 1)} given twice"
                             + ("" if spin == "none" else f" ({spin})"))
        ref = fs[0][1]
        for p, f in fs[1:]:
            if len(f["energy_ev"]) != len(ref["energy_ev"]) or \
                    not np.allclose(f["energy_ev"], ref["energy_ev"], atol=1e-4):
                raise ValueError(f"{p.name} and {fs[0][0].name} have different MOs: "
                                 "not from the same run and step")
        e, occ = ref["energy_ev"], ref["occupation"]
        occd = occ > OCC_TOL
        if not occd.any():
            raise ValueError("no occupied MO in the files")
        homo = float(e[occd].max())
        virt = e[~occd]
        lumo = float(virt.min()) if virt.size else None
        if lumo is None:
            warnings.append("no unoccupied MOs: the files have no LUMO. Rerun with &PDOS NLUMO "
                            "(hyogo cp2k_make_input pdos={'nlumo': 20})")
        frac = sum(f["total"] for _, f in fs)
        lo, hi = float(frac.min()), float(frac.max())
        if lo < 0.97:
            warnings.append(f"projections sum to {lo:.2f}-{hi:.2f} per MO, not ~1: a kind's "
                            "file is missing (pass every -k<n>- file of the run)")

        def character(sel):
            wsum = sum(float(f["total"][sel].sum()) for _, f in fs)
            kinds_out, chans = {}, {}
            for _, f in fs:
                t = float(f["total"][sel].sum())
                kinds_out[f["kind"]] = round(t / wsum, 3) if wsum else 0.0
                for j, ch in enumerate(f["channels"]):
                    key = f"{f['element']} {_channel_l(ch)}"
                    chans[key] = chans.get(key, 0.0) + float(f["weights"][sel, j].sum())
            chans = {k: round(v / wsum, 3) for k, v in sorted(chans.items(), key=lambda kv: -kv[1])
                     if wsum and v / wsum >= 0.01}
            return {"states": int(sel.sum()),
                    "kinds": dict(sorted(kinds_out.items(), key=lambda kv: -kv[1])),
                    "channels": chans}

        edges = {"homo_ev": round(homo, 4),
                 "valence": character(occd & (e >= homo - window_ev))}
        if lumo is not None:
            edges["lumo_ev"] = round(lumo, 4)
            edges["gap_ev"] = round(lumo - homo, 4)
            edges["conduction"] = character(~occd & (e <= lumo + window_ev))
            if lumo + window_ev > float(virt.max()):
                warnings.append(f"the conduction window reaches past the highest MO printed "
                                f"({float(virt.max()) - lumo:.2f} eV above the LUMO): print more "
                                "NLUMO for its character")
        out_spins[spin] = {"kinds": kinds, "n_mo": int(len(e)), "n_occupied": int(occd.sum()),
                           "e_fermi_ev": ref["e_fermi_ev"], **edges}

        tables.append((spin, kinds, e, [frac] + [f["total"] for _, f in fs]))

    # one energy zero for both spins: the highest occupied MO of either
    top = max(v["homo_ev"] for v in out_spins.values())
    grid = np.arange(emin_ev, emax_ev + de_ev / 2, de_ev)
    head = [f"# CP2K PDOS, Gaussian sigma {sigma_ev} eV, E - E(HOMO) in eV "
            f"(HOMO {top:.4f} eV), states/eV"]
    rows, names, cols = [], [], []
    for spin, kinds, e, ws in tables:
        tag = "" if spin == "none" else f"{spin}:"
        names += [f"{tag}total"] + [f"{tag}{k}" for k in kinds]
        g = np.exp(-0.5 * ((grid[:, None] - (e - top)[None, :]) / sigma_ev) ** 2) \
            / (sigma_ev * math.sqrt(2 * math.pi))
        cols += [g @ w for w in ws]
    head.append("# E " + " ".join(names))
    allc = np.array(cols).T
    for i, E in enumerate(grid):
        rows.append(f"{E:9.3f} " + " ".join(f"{v:12.5f}" for v in allc[i]))

    # the gap of the system: lowest LUMO of either spin minus highest HOMO
    gap = None
    if all("lumo_ev" in v for v in out_spins.values()):
        gap = round(min(v["lumo_ev"] for v in out_spins.values())
                    - max(v["homo_ev"] for v in out_spins.values()), 4)
    result = {"files": [p.name for p, _ in files], "spins": out_spins, "gap_ev": gap,
              "window_ev": window_ev, "sigma_ev": sigma_ev, "warnings": warnings}
    return "\n".join(head + rows) + "\n", result
