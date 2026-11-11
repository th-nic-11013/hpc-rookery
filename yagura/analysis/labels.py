"""Compare two labelled extxyz sets frame by frame (G38).

Two DFT results for the same structures (a relabel at other settings, a
different starting MAGMOM, +U, another code) are paired by geometry and
compared the way the MXene campaign's compare_magmom_test.py did it:

  * dE per atom, meV: (E_b - E_a) / N. A change of method shifts every frame
    alike, so frames are also judged against the median shift: a frame far
    from it sits in another state (spin, SCF solution) in one of the sets;
  * force difference per atom, |F_b - F_a| in meV/A, reported as its RMS and
    max over the atoms (and which atom has the max);
  * moments, when both sets carry per-atom moments: per element the net and
    the "opposed" sites (|m| >= 0.5 uB against the element's net, as
    hyogo's vasp_parse_output counts them), and the sites whose moment
    changed sign between the sets.

Numpy only. The extxyz reader is small and reads only what this needs; its
comment-line parser is a deliberate copy of hyogo/mace/dataset.py's (yagura
imports nothing from hyogo).
"""
import re
from collections import defaultdict

import numpy as np

ENERGY_KEYS = ("REF_energy", "energy", "dft_energy", "TotEnergy", "free_energy")
FORCES_KEYS = ("REF_forces", "forces", "dft_force", "force")
MOMENT_KEYS = ("REF_magmoms", "magmoms", "magmom", "REF_magmom", "magnetic_moments")
OPPOSED = 0.5   # uB, as hyogo/vasp/output.py
BUCKET = 0.01   # A: geometry pairing looks first among frames whose first atoms round alike

# deliberate copy of hyogo/mace/dataset.py's comment-line parser
_INFO = re.compile(r'([A-Za-z_][\w.\-]*)\s*=\s*("([^"]*)"|\'([^\']*)\'|\S+)')


def _info(comment: str) -> dict:
    out = {}
    for m in _INFO.finditer(comment):
        v = m.group(3) if m.group(3) is not None else m.group(4) if m.group(4) is not None else m.group(2)
        out[m.group(1)] = v
    return out


def read(text: str, source: str = "") -> list[dict]:
    """Frames of an extxyz text: {"index", "natoms", "symbols", "pos" (N, 3),
    "info" (comment-line strings), "arrays" {name: (N, k) float array}}."""
    lines, frames, i = text.splitlines(), [], 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        try:
            n = int(lines[i].split()[0])
        except ValueError:
            raise ValueError(f"{source or 'extxyz'} line {i + 1}: expected an atom count, got "
                             f"{lines[i][:40]!r}") from None
        if i + 2 + n > len(lines):
            raise ValueError(f"{source or 'extxyz'}: frame {len(frames)} is cut short")
        info = _info(lines[i + 1])
        props = info.get("Properties", "species:S:1:pos:R:3").split(":")
        if len(props) % 3:
            raise ValueError(f"{source}: Properties={info['Properties']!r} is not name:type:count triples")
        cols = [(props[k], props[k + 1], int(props[k + 2])) for k in range(0, len(props), 3)]
        rows = [l.split() for l in lines[i + 2:i + 2 + n]]
        if any(len(r) < sum(c for _, _, c in cols) for r in rows):
            raise ValueError(f"{source}: frame {len(frames)} has atom lines shorter than its Properties")
        arrays, symbols, at = {}, None, 0
        for name, kind, count in cols:
            if kind == "S" and name == "species":
                symbols = [r[at] for r in rows]
            elif kind in ("R", "I"):
                arrays[name] = np.array([r[at:at + count] for r in rows], float)
            at += count
        if symbols is None or "pos" not in arrays:
            raise ValueError(f"{source}: frame {len(frames)} has no species or pos column")
        frames.append({"index": len(frames), "natoms": n, "symbols": symbols,
                       "pos": arrays.pop("pos"), "info": info, "arrays": arrays})
        i += 2 + n
    if not frames:
        raise ValueError(f"{source or 'extxyz'} has no frames")
    return frames


def _key(frames, given, names, where, what, source):
    if given:
        missing = [f["index"] for f in frames if given not in f[where]]
        if missing:
            raise ValueError(f"{source}: {what} key {given!r} missing in frames {missing[:5]}")
        return given
    return next((k for k in names if all(k in f[where] for f in frames)), None)


def _energy(f, key):
    try:
        return float(f["info"][key])
    except ValueError:
        raise ValueError(f"frame {f['index']}: {key}={f['info'][key]!r} is not a number") from None


def _pair(a, b, mode, tol):
    """[(ia, ib)], unmatched b indices. geometry: same symbols and positions
    within tol (A, max over atoms); index: frame i with frame i, checked."""
    if mode == "index":
        pairs = [(i, i) for i in range(min(len(a), len(b)))]
        bad = [i for i, j in pairs if a[i]["symbols"] != b[j]["symbols"]
               or np.abs(a[i]["pos"] - b[j]["pos"]).max() > tol]
        if bad:
            raise ValueError(f"pair='index': frames {bad[:5]} differ in atoms or positions "
                             f"(> {tol} A); use pair='geometry'")
        return pairs, list(range(len(a), len(b)))
    buckets, by_atoms = defaultdict(list), defaultdict(list)
    for f in a:
        sig = (f["natoms"], tuple(f["symbols"]))
        by_atoms[sig].append(f["index"])
        buckets[sig + tuple(np.round(f["pos"][:2].ravel() / BUCKET).astype(int))].append(f["index"])
    used, pairs, unmatched = set(), [], []
    for f in b:
        sig = (f["natoms"], tuple(f["symbols"]))
        cand = buckets.get(sig + tuple(np.round(f["pos"][:2].ravel() / BUCKET).astype(int)), [])
        hit = None
        for pool in (cand, by_atoms.get(sig, [])):
            free = [i for i in pool if i not in used]
            if free:
                d = np.array([np.abs(a[i]["pos"] - f["pos"]).max() for i in free])
                if d.min() <= tol:
                    hit = free[int(d.argmin())]
                    break
        if hit is None:
            unmatched.append(f["index"])
        else:
            used.add(hit)
            pairs.append((hit, f["index"]))
    return pairs, unmatched


def _moments(symbols, m):
    out = {}
    for el in dict.fromkeys(symbols):
        ms = np.array([x for x, s in zip(m, symbols) if s == el])
        net = float(ms.sum())
        out[el] = {"net": round(net, 3),
                   "opposed": int(sum(1 for x in ms if abs(x) >= OPPOSED and (x > 0) != (net > 0)))}
    return out


def compare(a_text: str, b_text: str, a_name: str = "a", b_name: str = "b", pair: str = "geometry",
            tol: float = 1e-3, energy_key=None, forces_key=None, moment_key=None,
            force_rms_mev: float = 50.0, energy_mev: float = 5.0, max_rows: int = 20) -> tuple[dict, str]:
    """(result, a TSV with one row per pair). Flags a pair whose force RMS
    exceeds force_rms_mev, whose dE per atom is more than energy_mev from the
    median shift, or whose moments changed sign on any site."""
    if pair not in ("geometry", "index"):
        raise ValueError("pair is 'geometry' or 'index'")
    A, B = read(a_text, a_name), read(b_text, b_name)
    keys, warnings = {}, []
    for side, name, frames in (("a", a_name, A), ("b", b_name, B)):
        keys[side] = {
            "energy_key": _key(frames, energy_key, ENERGY_KEYS, "info", "energy", name),
            "forces_key": _key(frames, forces_key, FORCES_KEYS, "arrays", "forces", name),
            "moment_key": _key(frames, moment_key, MOMENT_KEYS, "arrays", "moment", name),
        }
    ka, kb = keys["a"], keys["b"]
    if not (ka["energy_key"] and kb["energy_key"]) and not (ka["forces_key"] and kb["forces_key"]):
        raise ValueError(f"no energy or forces present in every frame of both sets ({keys}); "
                         "pass energy_key / forces_key")
    pairs, unmatched = _pair(A, B, pair, tol)
    if not pairs:
        raise ValueError(f"no frame of {b_name} matches a frame of {a_name} (same atoms, positions "
                         f"within {tol} A)")
    rows = []
    for ia, ib in pairs:
        fa, fb, n = A[ia], B[ib], A[ia]["natoms"]
        r = {"a": ia, "b": ib, "natoms": n, "config_type": fb["info"].get("config_type") or fa["info"].get("config_type")}
        if ka["energy_key"] and kb["energy_key"]:
            r["de_mev_atom"] = (_energy(fb, kb["energy_key"]) - _energy(fa, ka["energy_key"])) / n * 1000
        if ka["forces_key"] and kb["forces_key"]:
            df = np.linalg.norm(fb["arrays"][kb["forces_key"]] - fa["arrays"][ka["forces_key"]], axis=1) * 1000
            k = int(df.argmax())
            r.update(df_rms_mev_a=float(np.sqrt((df ** 2).mean())), df_max_mev_a=float(df[k]),
                     df_max_atom=[k + 1, fa["symbols"][k]])
        if ka["moment_key"] and kb["moment_key"]:
            ma = fa["arrays"][ka["moment_key"]][:, 0]
            mb = fb["arrays"][kb["moment_key"]][:, 0]
            flip = [i + 1 for i in range(n) if max(abs(ma[i]), abs(mb[i])) >= OPPOSED
                    and (ma[i] > 0) != (mb[i] > 0)]
            r.update(moment_a=round(float(ma.sum()), 3), moment_b=round(float(mb.sum()), 3),
                     flipped_sites=flip, moments_a=_moments(fa["symbols"], ma),
                     moments_b=_moments(fb["symbols"], mb))
        rows.append(r)
    has_e = "de_mev_atom" in rows[0]
    shift = float(np.median([r["de_mev_atom"] for r in rows])) if has_e else None
    for r in rows:
        why = []
        if has_e:
            r["de_vs_median"] = r["de_mev_atom"] - shift
            if abs(r["de_vs_median"]) > energy_mev:
                why.append(f"dE {r['de_vs_median']:+.1f} meV/atom from the median shift")
        if r.get("df_rms_mev_a", 0) > force_rms_mev:
            why.append(f"force RMS {r['df_rms_mev_a']:.0f} meV/A")
        if r.get("flipped_sites"):
            why.append(f"{len(r['flipped_sites'])} site moments changed sign")
        r["reasons"] = why
    if has_e and len(rows) < 3:
        warnings.append(f"{len(rows)} pairs: the median shift is not a reference yet; read dE itself")
    if unmatched:
        warnings.append(f"{len(unmatched)} frames of {b_name} have no match in {a_name} "
                        f"(first: {unmatched[:5]})")
    if len(pairs) < len(A):
        warnings.append(f"{len(A) - len(pairs)} frames of {a_name} were not compared")
    if not (ka["moment_key"] and kb["moment_key"]):
        warnings.append("no per-atom moments in both sets: spin states are judged by energy and "
                        "forces only")

    def stats(name):
        v = np.array([r[name] for r in rows if name in r])
        return None if not v.size else {"mean": round(float(v.mean()), 3), "rms": round(float(np.sqrt((v ** 2).mean())), 3),
                                        "min": round(float(v.min()), 3), "max": round(float(v.max()), 3)}

    flagged = sorted((r for r in rows if r["reasons"]),
                     key=lambda r: (-r.get("df_rms_mev_a", 0), -abs(r.get("de_vs_median", 0))))

    def short(r):
        out = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()
               if k not in ("moments_a", "moments_b")}
        if "moments_a" in r:
            out["opposed"] = {el: [r["moments_a"][el]["opposed"], r["moments_b"][el]["opposed"]]
                              for el in r["moments_a"] if r["moments_a"][el]["opposed"] or r["moments_b"][el]["opposed"]}
            out["flipped_sites"] = r["flipped_sites"][:10]
        return out

    result = {"keys": keys, "pair": pair, "pairs": len(pairs), "frames": {"a": len(A), "b": len(B)},
              "median_shift_mev_atom": round(shift, 3) if shift is not None else None,
              "de_mev_atom": stats("de_mev_atom"), "de_vs_median": stats("de_vs_median"),
              "df_rms_mev_a": stats("df_rms_mev_a"), "df_max_mev_a": stats("df_max_mev_a"),
              "thresholds": {"force_rms_mev_a": force_rms_mev, "energy_mev_atom": energy_mev},
              "flagged_count": len(flagged), "flagged": [short(r) for r in flagged[:max_rows]],
              "warnings": warnings}
    cols = ["a", "b", "natoms", "config_type", "de_mev_atom", "de_vs_median", "df_rms_mev_a",
            "df_max_mev_a", "df_max_atom", "moment_a", "moment_b", "flipped_sites", "reasons"]
    lines = ["\t".join(cols)]
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c, "")
            cells.append(f"{v:.4f}" if isinstance(v, float) else
                         ";".join(map(str, v)) if isinstance(v, list) else str(v if v is not None else ""))
        lines.append("\t".join(cells))
    return result, "\n".join(lines) + "\n"
