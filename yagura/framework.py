"""Is the framework intact? Metal coordination, linker bonds, proton
transfer and displacement from an affine-scaled reference, on one frame or
a whole trajectory (extended xyz or a LAMMPS dump). Numpy only; streams
the frames, so a multi-GB dump is fine.

    {{FRONT_END_PYTHON}} -I framework.py frames.dump --specorder H C O Zr \
        [--frames ::10] [--reference relaxed.xyz] [--table frames.tsv]

The topology comes from a reference frame (reference_path, else the first
selected frame): every pair closer than its bond cutoff (`bonds`, default
C-H 1.35, C-C 1.85, C-O 1.75, O-H 1.35 A; metal-O below metal_o_cutoff).
Every analysed frame is then compared with it:

- overlap: any two atoms closer than `overlap` (0.7 A). Drops the frame.
- broken bond: a reference bond of a `bonds` type now longer than its
  cutoff. Drops the frame.
- new bond: more bonds of a `bonds` type within cutoff than the reference
  has (cross-linking, a hydrogen bonded twice). Drops the frame.
- proton transfer: an H whose nearest heavy atom is no longer its
  reference partner. Drops the frame.
- shared H: an H still nearest its partner but also within bond cutoff of
  a second heavy atom (a very short hydrogen bond, e.g. the mu3-OH proton
  half-way to a carboxylate O in a collapsed cell). Reported, not dropped:
  it is a real configuration, not a broken one. An extra X-H within cutoff
  is counted here, never as a new bond.
- metal-O: the coordination number of each metal (O within metal_o_cutoff)
  and the reference metal-O bonds now longer than the cutoff. Never drops
  a frame: short excursions (> 2.7 A for a few frames) are thermal and
  belong in a training set. Over a trajectory, each run of consecutive
  analysed frames in which one bond stays long is an excursion; runs longer
  than max_transient_frames are listed as persistent.
- displacement: each atom's distance from its reference position scaled
  affinely into the frame's cell (fractional coordinates kept), after
  removing the mean shift: rms and max, overall and for the metals.

Frame indices count from 0 in the file read. For an extended xyz that
structure_to_extxyz wrote from a dump, each row also carries that frame's
source (run label) and source_frame (its index in the dump).

Atoms must be in the same order in every frame and in the reference (a
LAMMPS dump sorted by id, and the data file it started from, are).
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

try:                                    # the package, or run as a script beside it
    from yagura import lammps_dump
except ImportError:                     # pragma: no cover
    import lammps_dump

BONDS = {"C-H": 1.35, "C-C": 1.85, "C-O": 1.75, "O-H": 1.35}
METAL_O = 2.7
OVERLAP = 0.7
MAX_TRANSIENT = 10
NONMETALS = {"H", "He", "B", "C", "N", "O", "F", "Ne", "Si", "P", "S", "Cl", "Ar",
             "Se", "Br", "Kr", "I", "Xe"}
LIST_CAP = 100


def _pair(p: str) -> tuple:
    a, b = (x.strip() for x in p.split("-"))
    return tuple(sorted((a, b)))


def _name(p: tuple) -> str:
    """C-H, O-H, C-O, Zr-O, C-C: H last, else O last, else alphabetical."""
    a, b = p
    for last in ("H", "O"):
        if a == last and b != last:
            return f"{b}-{a}"
        if b == last:
            return f"{a}-{b}"
    return f"{a}-{b}"


def _count(D, el, a, b, cut) -> int:
    """Pairs of elements a, b closer than cut (each pair once)."""
    if a == b:
        m = el == a
        return int(np.triu(D[np.ix_(m, m)] < cut, 1).sum())
    return int((D[np.ix_(el == a, el == b)] < cut).sum())


def read_xyz_frames(path, index: str = ":"):
    """Stream extended xyz frames (Lattice= on line 2): dicts like
    lammps_dump.frames yields, plus the comment's timestep / volume."""
    start, stop, step, single = lammps_dump.selection(index)
    if (start is not None and start < 0) or (stop is not None and stop < 0):
        with open(path) as f:
            n = int(f.readline())
            total = sum(1 for _ in f) // (n + 2) + (1 if n else 0)
        start = start + total if start is not None and start < 0 else start
        stop = stop + total if stop is not None and stop < 0 else stop
    start = start or 0
    if single:
        stop = start + 1
    with open(path) as f:
        k = -1
        while True:
            head = f.readline()
            if not head.strip():
                break
            k += 1
            if stop is not None and k >= stop:
                break
            n = int(head)
            com = f.readline()
            raw = [f.readline() for _ in range(n)]
            if k < start or (k - start) % step:
                continue
            m = re.search(r'Lattice="([^"]+)"', com)
            if not m:
                raise ValueError(f"{path} frame {k}: line 2 has no Lattice=\"...\" (need extended xyz)")
            tok = "".join(raw).split()
            if len(tok) < 4 * n:
                raise ValueError(f"{path} frame {k} is cut short")
            rows = [l.split() for l in raw]
            ts = re.search(r"\btimestep=(-?\d+)", com)
            sf = re.search(r"(?<![\w])frame=(\d+)", com)
            so = re.search(r"(?<![\w])source=([^\s\"]+)", com)
            yield {"frame": k, "timestep": int(ts.group(1)) if ts else None,
                   "source": so.group(1) if so else None,
                   "source_frame": int(sf.group(1)) if sf else None,
                   "cell": np.array(m.group(1).split(), float).reshape(3, 3),
                   "symbols": np.array([r[0] for r in rows]),
                   "positions": np.array([r[1:4] for r in rows], float)}
            if single:
                return


def iter_frames(path, specorder=None, index: str = ":"):
    if lammps_dump.is_dump(path):
        return lammps_dump.frames(path, specorder, index)
    return read_xyz_frames(path, index)


def _distances(fr):
    """Minimum-image distance matrix (float32) and fractional coordinates."""
    H = fr["cell"]
    F = fr["positions"] @ np.linalg.inv(H)
    f32 = F.astype(np.float32)
    d = []
    for k in range(3):                  # 2-D arrays per axis: much faster than one N x N x 3
        dk = f32[:, k][:, None] - f32[:, k][None, :]
        dk -= np.rint(dk)
        d.append(dk)
    D2 = np.zeros_like(d[0])
    for c in range(3):                  # Cartesian component c = sum_k d_k H[k, c]
        xc = None
        for k in range(3):
            if H[k, c]:
                t = d[k] * np.float32(H[k, c])
                xc = t if xc is None else xc + t
        if xc is not None:
            D2 += xc * xc
    D = np.sqrt(D2)
    np.fill_diagonal(D, np.inf)
    return D, F


def _widths(H):
    v = abs(np.linalg.det(H))
    return [v / np.linalg.norm(np.cross(H[(i + 1) % 3], H[(i + 2) % 3])) for i in range(3)]


class _Topology:
    """Reference bonds and partners, from one frame."""

    def __init__(self, fr, bonds, metals, metal_o_cutoff):
        self.symbols = fr["symbols"]
        self.F0 = fr["positions"] @ np.linalg.inv(fr["cell"])
        D, _ = _distances(fr)
        el = self.symbols
        self.types = {}                         # name -> (i, j, cutoff)
        for p, cut in bonds.items():
            a, b = p
            m = np.triu(D < cut, 1) & (((el[:, None] == a) & (el[None, :] == b))
                                       | ((el[:, None] == b) & (el[None, :] == a)))
            i, j = np.nonzero(m)
            self.types[_name(p)] = (i, j, cut)
        self.metals = np.isin(el, list(metals))
        self.oxygen = el == "O"
        m = (self.metals[:, None] & self.oxygen[None, :]) & (D < metal_o_cutoff)
        self.mo = np.nonzero(m)
        self.heavy_idx = np.nonzero(el != "H")[0]
        self.hyd = np.nonzero(el == "H")[0]
        hd = D[np.ix_(self.hyd, self.heavy_idx)]
        self.h_partner = self.heavy_idx[hd.argmin(axis=1)] if len(self.hyd) and len(self.heavy_idx) \
            else self.hyd
        self.h_cut = {(b if a == "H" else a): c for (a, b), c in bonds.items() if "H" in (a, b)}


def check(path, specorder=None, index: str = ":", reference_path=None, metals=None,
          metal_o_cutoff: float = METAL_O, bonds: dict | None = None, overlap: float = OVERLAP,
          max_transient_frames: int = MAX_TRANSIENT, dt_fs: float | None = None,
          table_path=None) -> dict:
    """The checks above over the selected frames; see the module docstring."""
    bonds = {_pair(p): float(c) for p, c in (bonds or BONDS).items()}
    if any("H" == a == b for a, b in bonds):
        raise ValueError("H-H is not a framework bond")
    warnings = []
    frames = iter_frames(path, specorder, index)
    ref = None
    if reference_path is not None:
        ref = next(iter_frames(reference_path, specorder, "0"))
        ref_label = f"{reference_path} (first frame)"
    topo = None
    shown, buf, flagged, reasons, frame_ids, shared_frames = [], [], [], {}, [], []
    btype_stats = {}
    mo_long_frames, mo_max_long, mo_max_len, cn_hist = 0, 0, 0.0, {}
    run = None
    runs = []                                   # (bond k, first row, last row)
    disp_rms_max = disp_max = 0.0
    vols, steps = [], []
    n = 0
    for fr in frames:
        if topo is None:
            if ref is None:
                ref, ref_label = fr, f"frame {fr['frame']} of the input (the first analysed)"
            if metals is None:
                metals = sorted({str(s) for s in ref["symbols"]} - NONMETALS)
            if len(ref["symbols"]) != len(fr["symbols"]) or (ref["symbols"] != fr["symbols"]).any():
                raise ValueError("the reference and the frames list different atoms (or a different "
                                 "order): the reference must be the structure the run started from")
            topo = _Topology(ref, bonds, metals, metal_o_cutoff)
            run = np.zeros(len(topo.mo[0]), int)
            btype_stats = {t: {"n": int(len(i)), "cutoff": c, "min": None, "max": None,
                               "frames_broken": 0} for t, (i, j, c) in topo.types.items()}
        elif len(fr["symbols"]) != len(topo.symbols) or (fr["symbols"] != topo.symbols).any():
            raise ValueError(f"frame {fr['frame']} lists different atoms from the reference")
        H = fr["cell"]
        if not (np.isfinite(fr["positions"]).all() and np.isfinite(H).all()):
            raise ValueError(f"frame {fr['frame']} has non-finite coordinates or cell (a run that "
                             "blew up?); exclude it with index")
        if min(_widths(H)) < 2 * max([metal_o_cutoff, *bonds.values()]):
            warnings.append(f"frame {fr['frame']}: the cell is narrower than twice the largest "
                            "cutoff; minimum image may miss pairs")
        D, F = _distances(fr)
        el = topo.symbols
        why = []
        # overlap
        k = int(np.argmin(D))
        i0, j0 = divmod(k, D.shape[1])
        dmin = float(D[i0, j0])
        if dmin < overlap:
            why.append("overlap")
        # reference bonds, broken and new
        row_b = {}
        for t, (i, j, cut) in topo.types.items():
            st = btype_stats[t]
            if not len(i):
                continue
            d = D[i, j]
            lo, hi = float(d.min()), float(d.max())
            st["min"] = lo if st["min"] is None else min(st["min"], lo)
            st["max"] = hi if st["max"] is None else max(st["max"], hi)
            broken = int((d > cut).sum())
            new = max(0, _count(D, el, *t.split("-"), cut) - (len(i) - broken))
            if broken:
                st["frames_broken"] += 1
                why.append(f"broken {t}")
            if new and "H" not in t.split("-"):     # an extra X-H is a shared H, below
                why.append(f"new {t}")
            row_b[t] = (round(hi, 3), broken, new)
        # proton transfer: nearest heavy atom of each H
        transfers = shared = 0
        if len(topo.hyd) and len(topo.heavy_idx) > 1:
            heavy = topo.heavy_idx
            Dh = D[np.ix_(topo.hyd, heavy)]
            two = np.argpartition(Dh, 1, axis=1)[:, :2]
            d2 = np.take_along_axis(Dh, two, axis=1)
            order = np.argsort(d2, axis=1)
            near = heavy[np.take_along_axis(two, order, axis=1)]
            d2 = np.take_along_axis(d2, order, axis=1)
            transfers = int((near[:, 0] != topo.h_partner).sum())
            if transfers:
                why.append("proton transfer")
            cut2 = np.array([topo.h_cut.get(str(e), 0.0) for e in el[near[:, 1]]])
            shared = int((d2[:, 1] < cut2).sum())
            if shared:
                shared_frames.append(fr["frame"])
        # metal-O
        mi, oi = topo.mo
        n_long, mo_len = 0, 0.0
        if len(mi):
            d = D[mi, oi]
            long_ = d > metal_o_cutoff
            n_long, mo_len = int(long_.sum()), float(d.max())
            mo_long_frames += bool(n_long)
            mo_max_long = max(mo_max_long, n_long)
            mo_max_len = max(mo_max_len, mo_len)
            for b in np.nonzero(~long_ & (run > 0))[0]:
                runs.append((int(b), int(n - run[b]), n - 1))
            run = np.where(long_, run + 1, 0)
        cn = (D[np.ix_(topo.metals, topo.oxygen)] < metal_o_cutoff).sum(axis=1)
        for c, m in zip(*np.unique(cn, return_counts=True)):
            cn_hist[int(c)] = cn_hist.get(int(c), 0) + int(m)
        # displacement from the affine-scaled reference
        U = F - topo.F0
        U -= np.round(U)
        U = U @ H
        U -= U.mean(axis=0)
        u = np.linalg.norm(U, axis=1)
        rms, umax = float(np.sqrt((u ** 2).mean())), float(u.max())
        rms_m = float(np.sqrt((u[topo.metals] ** 2).mean())) if topo.metals.any() else None
        disp_rms_max, disp_max = max(disp_rms_max, rms), max(disp_max, umax)

        vol = float(abs(np.linalg.det(H)))
        vols.append(vol)
        steps.append(fr["timestep"])
        row = {"frame": fr["frame"], "timestep": fr["timestep"]}
        if fr.get("source_frame") is not None:      # an extxyz written from a dump
            row.update(source=fr.get("source"), source_frame=fr["source_frame"])
        if dt_fs is not None and fr["timestep"] is not None:
            row["time_ps"] = round(fr["timestep"] * dt_fs / 1000, 6)
        row.update({"volume": round(vol, 2), "min_dist": round(dmin, 3),
                    "min_pair": f"{el[i0]}{i0}-{el[j0]}{j0}",
                    "metal_cn_min": int(cn.min()) if len(cn) else None,
                    "metal_cn_max": int(cn.max()) if len(cn) else None,
                    "metal_o_long": n_long, "metal_o_max": round(mo_len, 3),
                    "bonds": {t: {"max": v[0], "broken": v[1], "new": v[2]} for t, v in row_b.items()},
                    "proton_transfers": transfers, "shared_h": shared,
                    "disp_rms": round(rms, 3), "disp_max": round(umax, 3),
                    "disp_rms_metal": None if rms_m is None else round(rms_m, 3),
                    "drop": bool(why), "reasons": why})
        if len(shown) <= 20:
            shown.append(row)
        frame_ids.append(fr["frame"])
        if why:
            flagged.append(fr["frame"])
            for r in why:
                reasons[r] = reasons.get(r, 0) + 1
        n += 1
        if table_path is not None:
            buf.append(row)
            if len(buf) >= 1000:
                _table(table_path, buf, append=n > len(buf))
                buf = []
    if topo is None:
        raise ValueError(f"index {index!r} selects no frame of {path}")
    for b in np.nonzero(run > 0)[0]:
        runs.append((int(b), int(n - run[b]), n - 1))
    if table_path is not None:
        _table(table_path, buf, append=n > len(buf))

    lens = [last - first + 1 for _, first, last in runs]
    persistent = [r for r, L in zip(runs, lens) if L > max_transient_frames]
    mi, oi = topo.mo
    out = {
        "path": str(path), "frames_analysed": n,
        "reference": ref_label,
        "timestep_range": [steps[0], steps[-1]] if steps[0] is not None else None,
        "volume_range": [round(min(vols), 2), round(max(vols), 2)],
        "composition": {str(e): int(c) for e, c in zip(*np.unique(topo.symbols, return_counts=True))},
        "frames_dropped": len(flagged),
        "drop_frames": flagged[:LIST_CAP],
        "drop_reasons": reasons,
        "bonds": {t: {**s, "min": None if s["min"] is None else round(s["min"], 3),
                      "max": None if s["max"] is None else round(s["max"], 3)}
                  for t, s in btype_stats.items()},
        "metal_o": {"metals": list(metals), "cutoff": metal_o_cutoff, "reference_bonds": int(len(mi)),
                    "frames_with_long": mo_long_frames, "max_long_at_once": mo_max_long,
                    "max_length": round(mo_max_len, 3),
                    "cn_histogram": {str(k): v for k, v in sorted(cn_hist.items())},
                    "excursions": len(runs), "longest_frames": max(lens, default=0),
                    "max_transient_frames": max_transient_frames,
                    "persistent": [{"metal": f"{topo.symbols[mi[b]]}{mi[b]}", "o": f"O{oi[b]}",
                                    "frames": last - first + 1,
                                    "first_frame": frame_ids[first], "last_frame": frame_ids[last]}
                                   for b, first, last in persistent[:LIST_CAP]],
                    "persistent_count": len(persistent)},
        "displacement": {"rms_max": round(disp_rms_max, 3), "max": round(disp_max, 3)},
        "proton_transfer_frames": reasons.get("proton transfer", 0),
        "shared_h_frames": len(shared_frames), "shared_h_first": shared_frames[:LIST_CAP],
        "ok": not flagged,
        "warnings": sorted(set(warnings))[:20],
    }
    if n == 1 and reference_path is None:
        out["warnings"].append("one frame and no reference_path: bonds and H partners are judged "
                               "against the frame itself, so nothing can be broken; give "
                               "reference_path (the structure the run started from) to compare")
    if n <= 20:
        out["frames"] = shown
    if table_path is not None:
        out["table"] = str(table_path)
    return out


COLUMNS = ["frame", "source", "source_frame", "timestep", "time_ps", "volume", "min_dist", "min_pair", "metal_cn_min",
           "metal_cn_max", "metal_o_long", "metal_o_max", "proton_transfers", "shared_h", "disp_rms",
           "disp_max", "disp_rms_metal", "drop", "reasons"]


def _table(path, rows, append):
    if not rows:
        return
    btypes = list(rows[0]["bonds"])
    cols = [c for c in COLUMNS if c in rows[0]]
    head = cols[:-2] + [f"{t}_{k}" for t in btypes for k in ("max", "broken", "new")] + cols[-2:]
    with open(path, "a" if append else "w") as f:
        if not append:
            f.write("\t".join(head) + "\n")
        for r in rows:
            vals = [r.get(c) for c in cols[:-2]] + [r["bonds"][t][k] for t in btypes
                                                    for k in ("max", "broken", "new")]
            vals += [int(r["drop"]), ";".join(r["reasons"])]
            f.write("\t".join("" if v is None else str(v) for v in vals) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--specorder", nargs="+")
    ap.add_argument("--frames", default=":")
    ap.add_argument("--reference")
    ap.add_argument("--metal-o-cutoff", type=float, default=METAL_O)
    ap.add_argument("--table")
    ap.add_argument("--dt-fs", type=float)
    a = ap.parse_args(argv)
    try:
        r = check(a.path, a.specorder, a.frames, a.reference, metal_o_cutoff=a.metal_o_cutoff,
                  dt_fs=a.dt_fs, table_path=a.table)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    r.pop("frames", None)
    print(json.dumps(r))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
