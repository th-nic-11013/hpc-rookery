"""Read a LAMMPS text dump (dump custom / atom) frame by frame, and write
the frames as extended xyz. Numpy only; never holds more than one frame.

    {{FRONT_END_PYTHON}} -I lammps_dump.py frames.dump out.xyz --specorder H C O Zr \
        [--frames ::50] [--dt-fs 0.5]

A .gz dump is read through gzip. Our MLMD dumps are multi-GB (20,000 frames x 456 atoms), so the reader
streams: frames outside the selection are skipped line by line, not parsed.

Types become elements through `specorder` (type 1, 2, ... in the order of
`pair_coeff * * ...`), unless the dump has an `element` column. Positions
come from x y z (wrapped), xu yu zu (unwrapped) or xs ys zs (scaled), and
atoms are sorted by id when there is an id column. Orthogonal, restricted
triclinic (xy xz yz) and general triclinic (abc origin) boxes are read;
the box origin is subtracted, so positions are relative to the cell's
corner, and every frame is wrapped into the cell.

Each written frame's comment line carries, besides Lattice= and pbc=:
timestep (the dump's), time_ps (timestep x dt, only when dt_fs is given),
volume (A^3), source (a run label without spaces, default the dump's file
stem; give the same label to every chunk of one run), frame (the frame's
index in the dump, from 0, before any stride), source_path (the dump's
absolute path) and config_type (when given, a stratum label such as
P1-300K). These are the keys hyogo's mlip_select_frames (G50) reads by
default, so a selection can stratify by volume or time and trace a frame
back to its dump.
"""
import argparse
import gzip
import json
import re
import sys
from pathlib import Path

import numpy as np

#: atomic numbers (index = Z), for the electron count without ASE
SYMBOLS = ("X H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
           "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr "
           "Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr "
           "Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr").split()
Z = {s: i for i, s in enumerate(SYMBOLS) if i}

_POS = (("x", "y", "z", False), ("xu", "yu", "zu", False),
        ("xs", "ys", "zs", True), ("xsu", "ysu", "zsu", True))


def selection(index: str):
    """ASE-like index -> (start, stop, step, single). "-1" is the last frame,
    "5" the sixth, "0:800:50" / "::10" / "100:" slices."""
    s = str(index).strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s), None, 1, True
    parts = s.split(":")
    if len(parts) not in (2, 3) or not all(re.fullmatch(r"\s*-?\d*\s*", p) for p in parts):
        raise ValueError(f"index is a frame number ('-1' = last) or START:STOP:STEP like '::50' "
                         f"or '100:'; got {index!r}")
    vals = [int(p) if p.strip() else None for p in parts] + [None] * (3 - len(parts))
    if vals[2] is not None and vals[2] <= 0:
        raise ValueError("the index step must be positive")
    return vals[0], vals[1], vals[2] or 1, False


def _open(path, mode="r"):
    """The file, or its gzip stream for a .gz name (dump custom/gz)."""
    return gzip.open(path, mode + "t" if mode == "r" else mode) if str(path).endswith(".gz") \
        else open(path, mode)


def is_dump(path) -> bool:
    """True when the file starts like a LAMMPS text dump (plain or .gz)."""
    try:
        with _open(path, "rb") as f:
            return f.readline().strip() == b"ITEM: TIMESTEP"
    except OSError:
        return False


def count_frames(path) -> int:
    n = 0
    with _open(path, "rb") as f:
        for line in f:
            if line.startswith(b"ITEM: TIMESTEP"):
                n += 1
    return n


def _box(header: str, rows: list[str]):
    """(cell rows a, b, c; origin) from the BOX BOUNDS item and its three lines."""
    v = np.array([r.split() for r in rows], float)
    if "abc origin" in header:                      # general triclinic (LAMMPS 2024+)
        return v[:, :3].copy(), v[:, 3].copy()
    if v.shape[1] == 3:                             # restricted triclinic: bounds + tilt
        (xlob, xhib, xy), (ylob, yhib, xz), (zlo, zhi, yz) = v
        xlo = xlob - min(0.0, xy, xz, xy + xz)
        xhi = xhib - max(0.0, xy, xz, xy + xz)
        ylo, yhi = ylob - min(0.0, yz), yhib - max(0.0, yz)
    else:
        (xlo, xhi), (ylo, yhi), (zlo, zhi) = v[:, :2]
        xy = xz = yz = 0.0
    cell = np.array([[xhi - xlo, 0, 0], [xy, yhi - ylo, 0], [xz, yz, zhi - zlo]], float)
    return cell, np.array([xlo, ylo, zlo])


def frames(path, specorder=None, index: str = ":"):
    """Yield the selected frames as dicts: frame (index in the file), timestep,
    cell (3x3, rows), symbols (array), positions (N x 3, A, wrapped into the
    cell from its corner). A negative index needs one extra pass to count
    the frames."""
    start, stop, step, single = selection(index)
    total = None
    if (start is not None and start < 0) or (stop is not None and stop < 0):
        total = count_frames(path)
        if start is not None and start < 0:
            start += total
        if stop is not None and stop < 0:
            stop += total
        if single and not 0 <= start < total:
            raise ValueError(f"frame {index} is outside the {total} frames of {path}")
    start = start or 0
    if single:
        stop = start + 1
    types_seen: dict | None = None
    with _open(path) as f:
        k = -1
        while True:
            line = f.readline()
            if not line:
                break
            if not line.startswith("ITEM: TIMESTEP"):
                if line.strip():
                    raise ValueError(f"{path}: expected 'ITEM: TIMESTEP', got {line.strip()[:60]!r}")
                continue
            k += 1
            if stop is not None and k >= stop:
                break
            ts = int(f.readline().split()[0])
            if f.readline().strip() != "ITEM: NUMBER OF ATOMS":
                raise ValueError(f"{path} frame {k}: no NUMBER OF ATOMS item")
            n = int(f.readline())
            bh = f.readline()
            if not bh.startswith("ITEM: BOX BOUNDS"):
                raise ValueError(f"{path} frame {k}: no BOX BOUNDS item")
            box = [f.readline() for _ in range(3)]
            ah = f.readline()
            if not ah.startswith("ITEM: ATOMS"):
                raise ValueError(f"{path} frame {k}: no ATOMS item")
            want = k >= start and (k - start) % step == 0
            if not want:
                for _ in range(n):
                    f.readline()
                continue
            cols = ah.split()[2:]
            raw = [f.readline() for _ in range(n)]
            if not raw[-1]:
                raise ValueError(f"{path} frame {k} is cut short (the run is still writing it?)")
            yield _parse(path, k, ts, n, bh, box, cols, raw, specorder)
            if single:
                return
    if single and k < start:
        raise ValueError(f"frame {start} is beyond the {k + 1} frames of {path}")


def _parse(path, k, ts, n, bh, box, cols, raw, specorder):
    cell, origin = _box(bh, box)
    tok = np.array("".join(raw).split(), dtype=object).reshape(n, len(cols))
    for cx, cy, cz, scaled in _POS:
        if cx in cols and cy in cols and cz in cols:
            P = tok[:, [cols.index(cx), cols.index(cy), cols.index(cz)]].astype(float)
            break
    else:
        raise ValueError(f"{path}: the dump has no position columns (x y z, xu yu zu or xs ys zs); "
                         f"columns are {cols}")
    if "element" in cols:
        sym = tok[:, cols.index("element")].astype(str)
    elif "type" in cols:
        if not specorder:
            raise ValueError("the dump has atom types, not elements: give specorder, the element "
                             "of type 1, 2, ... as in pair_coeff * * (e.g. [\"H\", \"C\", \"O\", \"Zr\"])")
        t = tok[:, cols.index("type")].astype(int)
        if t.min() < 1 or t.max() > len(specorder):
            raise ValueError(f"the dump has types {t.min()}..{t.max()} but specorder names "
                             f"{len(specorder)}: {list(specorder)}")
        sym = np.asarray(specorder, dtype=str)[t - 1]
    else:
        raise ValueError(f"{path}: the dump has neither a type nor an element column")
    unknown = sorted(set(sym.tolist()) - set(Z))
    if unknown:
        raise ValueError(f"unknown element(s) {unknown}: specorder (or the element column) must "
                         "name elements, e.g. [\"H\", \"C\", \"O\", \"Zr\"]")
    if "id" in cols:
        order = np.argsort(tok[:, cols.index("id")].astype(int), kind="stable")
        P, sym = P[order], sym[order]
    frac = P if scaled else (P - origin) @ np.linalg.inv(cell)
    frac -= np.floor(frac)
    return {"frame": k, "timestep": ts, "cell": cell, "symbols": sym, "positions": frac @ cell}


def comment(fr: dict, source: str | None = None, dt_fs: float | None = None,
            source_path: str | None = None, config_type: str | None = None) -> str:
    cell = fr["cell"]
    out = ['Lattice="' + " ".join(f"{x:.8f}" for x in cell.ravel()) + '"',
           "Properties=species:S:1:pos:R:3", f"timestep={fr['timestep']}"]
    if dt_fs is not None:
        out.append(f"time_ps={fr['timestep'] * dt_fs / 1000:.6f}")
    out.append(f"volume={abs(np.linalg.det(cell)):.4f}")
    if source is not None:
        out += [f"source={source}", f"frame={fr['frame']}"]
    if source_path is not None:
        out.append(f'source_path="{source_path}"')
    if config_type is not None:
        out.append(f"config_type={config_type}")
    out.append('pbc="T T T"')
    return " ".join(out)


def write_extxyz(dump, out, specorder=None, index: str = "-1", dt_fs: float | None = None,
                 time_range_ps=None, source: str | None = None,
                 config_type: str | None = None) -> dict:
    """Stream the selected frames of `dump` to `out` (extended xyz)."""
    if time_range_ps is not None and dt_fs is None:
        raise ValueError("time_range_ps needs dt_fs (the MD timestep in fs)")
    for name, v in (("source", source), ("config_type", config_type)):
        if v is not None and not re.fullmatch(r"[A-Za-z0-9_.+\-]+", v):
            raise ValueError(f"{name} is a label of letters, digits and _ . + - (no spaces); got {v!r}")
    try:
        return _write(dump, out, specorder, index, dt_fs, time_range_ps, source, config_type)
    except BaseException:
        Path(out).unlink(missing_ok=True)      # no half-written file left behind
        raise


def _write(dump, out, specorder, index, dt_fs, time_range_ps, source, config_type):
    if time_range_ps is not None:
        t0, t1 = (list(time_range_ps) + [None, None])[:2]
    src = str(Path(dump).expanduser().resolve())
    if source is None:
        source = re.sub(r"[^A-Za-z0-9_.+\-]", "_", Path(dump).name.split(".")[0]) or "dump"
    n_out, first, last, vols, symbols = 0, None, None, [], None
    with open(out, "w") as w:
        for fr in frames(dump, specorder, index):
            if time_range_ps is not None:
                t = fr["timestep"] * dt_fs / 1000
                if (t0 is not None and t < t0) or (t1 is not None and t > t1):
                    continue
            if symbols is None:
                symbols = fr["symbols"]
            elif len(fr["symbols"]) != len(symbols) or (fr["symbols"] != symbols).any():
                raise ValueError(f"frame {fr['frame']} has different atoms from the first frame written")
            w.write(f"{len(symbols)}\n{comment(fr, source, dt_fs, src, config_type)}\n")
            w.write("".join(f"{s} {x:.8f} {y:.8f} {z:.8f}\n"
                            for s, (x, y, z) in zip(symbols, fr["positions"])))
            v = float(abs(np.linalg.det(fr["cell"])))
            vols.append(v)
            stamp = {"frame": fr["frame"], "timestep": fr["timestep"], "volume": round(v, 2)}
            if dt_fs is not None:
                stamp["time_ps"] = round(fr["timestep"] * dt_fs / 1000, 6)
            first = first or stamp
            last = stamp
            n_out += 1
    if not n_out:
        raise ValueError(f"index {index!r}" + (f" and time_range_ps {list(time_range_ps)}"
                                               if time_range_ps is not None else "")
                         + f" select no frame of {dump}")
    el, cnt = np.unique(symbols, return_counts=True)
    return {"out": str(out), "frames_written": n_out, "natoms": int(len(symbols)),
            "formula": "".join(f"{e}{c}" for e, c in zip(el, cnt)),
            "composition": {str(e): int(c) for e, c in zip(el, cnt)},
            "source": source, "first": first, "last": last,
            "volume_range": [round(min(vols), 2), round(max(vols), 2)]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump")
    ap.add_argument("out")
    ap.add_argument("--specorder", nargs="+", help="element of type 1, 2, ...")
    ap.add_argument("--frames", default=":", help="frame or START:STOP:STEP (default all)")
    ap.add_argument("--dt-fs", type=float, help="MD timestep in fs, for time_ps")
    a = ap.parse_args(argv)
    try:
        r = write_extxyz(a.dump, a.out, a.specorder, a.frames, a.dt_fs)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(json.dumps(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
