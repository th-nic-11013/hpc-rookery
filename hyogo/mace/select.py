"""Pick MLIP training frames from a pool of MD frames (extended XYZ).

Steps, in this order: hold out whole blocks (time windows per run), drop
frames already selected or labelled, sort what is left into strata
(stratum keys x volume bins, with a quota per stratum key), pick inside
each volume bin by farthest-point sampling (FPS) on descriptors, or on the
volume alone when there are none, or by a per-frame score (round 2:
committee disagreement) when one is given, never closer than
min_spacing_ps to another pick from the same run; then add a uniformly
spaced control set. Seeded, so the same inputs give the same picks.

The pool is streamed, not loaded: a 60,000-frame pool of 456-atom cells is
~1.6 GB of text. Per frame only the comment line, a sha256 of the frame's
own lines and a geometry fingerprint are kept; the picked frames are read
again on a second pass and written byte for byte, with the selection's
keys appended to the comment line.

Frame identity is the sha256 of the frame's own lines (as
mace_make_finetune hashes them). A labelled frame (cp2k_make_labels) is a
new text, so it is matched to the pool by geometry instead: same atom
count, volume within 0.01 %, and its first atoms within 1e-3 A (minimum
image) of a pool frame's.

Descriptors (optional): one row per pool frame, from
codes/mace/finetune/scripts/mace_descriptors.py (per-element means of
the base model's invariant node features). Its sidecar json names the
pool files it was computed on; they must be the pool given here.
"""
import bisect
import fnmatch
import gzip
import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np

from hyogo.mace.dataset import _columns, _info

KEYS = {"source": "source", "frame": "frame", "time": "time_ps", "volume": "volume"}
SEL_KEYS = ("pool_sha256", "pool_file", "pool_index", "stratum", "picked_by")
FP_ATOMS = 3          # atoms in the geometry fingerprint
FP_TOL_A = 1e-3       # position tolerance of a geometry match (A)
VOL_RTOL = 1e-4       # relative volume tolerance of a geometry match
HOLDOUT_FIELDS = {"source", "match", "last_ps", "first_ps", "from_ps", "to_ps"}


# --- reading -----------------------------------------------------------------

def _open(path: Path):
    return gzip.open(path, "rb") if path.suffix == ".gz" else open(path, "rb")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _frames(path: Path):
    """(index, natoms, comment, frame bytes) for each frame, streamed.
    Line ends are normalised to \\n, as dataset.read_frames does."""
    with _open(path) as fh:
        k = 0
        for line in fh:
            if not line.strip():
                continue
            try:
                n = int(line.split()[0])
            except ValueError:
                raise ValueError(f"{path}: frame {k}: expected an atom count, got "
                                 f"{line[:40].decode(errors='replace')!r}") from None
            body = [line.rstrip(b"\r\n") + b"\n"]
            for _ in range(n + 1):
                nxt = fh.readline()
                if not nxt:
                    raise ValueError(f"{path}: frame {k} is cut short ({n} atoms announced)")
                body.append(nxt.rstrip(b"\r\n") + b"\n")
            yield k, n, body[1].decode(errors="replace").rstrip("\n"), body
            k += 1


def _cell(info: dict, where: str):
    if "Lattice" not in info:
        return None
    try:
        c = np.array([float(v) for v in info["Lattice"].split()]).reshape(3, 3)
    except ValueError:
        raise ValueError(f"{where}: Lattice is not 9 numbers") from None
    return c


def _fingerprint(info: dict, body: list, where: str) -> np.ndarray:
    cols = _columns(info["Properties"]) if "Properties" in info else [("species", "S", 1), ("pos", "R", 3)]
    at = next((k for k, (name, _, _) in enumerate(cols) if name == "pos"), None)
    if at is None:
        raise ValueError(f"{where}: no pos column in Properties")
    off = sum(c for _, _, c in cols[:at])
    rows = []
    for line in body[2:2 + FP_ATOMS]:
        s = line.split()
        rows.append([float(v) for v in s[off:off + 3]])
    return np.array(rows, dtype=float)


def read_pool(paths: list[Path]) -> dict:
    """Index the pool: per frame its file, index, comment info, sha256,
    atom count, cell and fingerprint positions. Nothing else is kept."""
    files, rows = [], []
    for fid, p in enumerate(paths):
        n0 = len(rows)
        for k, n, comment, body in _frames(p):
            info = _info(comment)
            where = f"{p.name} frame {k}"
            rows.append({"file": fid, "index": k, "natoms": n, "info": info,
                         "sha256": info.get("pool_sha256") or hashlib.sha256(b"".join(body)).hexdigest(),
                         "cell": _cell(info, where), "fp": _fingerprint(info, body, where)})
        if len(rows) == n0:
            raise ValueError(f"{p}: no frames")
        files.append({"path": str(p), "name": p.name, "sha256": file_sha256(p), "frames": len(rows) - n0})
    return {"files": files, "frames": rows}


def read_exclude(paths: list[Path]) -> tuple[set, list]:
    """Frames to skip: sha256s from earlier manifests (json) or from frames
    carrying pool_sha256; geometry fingerprints of any other frame
    (labelled ones)."""
    shas, geoms = set(), []
    for p in paths:
        if p.suffix == ".json":
            try:
                man = json.loads(p.read_text())
                shas |= {f["sha256"] for f in man["frames"]}
            except (ValueError, KeyError, TypeError):
                raise ValueError(f"{p}: not a mlip_select_frames manifest (no frames[].sha256)") from None
            continue
        for k, n, comment, body in _frames(p):
            info = _info(comment)
            if "pool_sha256" in info:
                shas.add(info["pool_sha256"])
                continue
            cell = _cell(info, f"{p.name} frame {k}")
            if cell is None:
                raise ValueError(f"{p.name} frame {k}: no pool_sha256 and no Lattice, so it cannot "
                                 "be matched to the pool")
            geoms.append({"where": f"{p.name}:{k}", "natoms": n, "cell": cell,
                          "volume": abs(np.linalg.det(cell)),
                          "fp": _fingerprint(info, body, f"{p.name} frame {k}")})
    return shas, geoms


def write_frames(paths: list[Path], picks: dict, out_path: Path) -> None:
    """Second pass: write the picked frames, in pick order, each with its
    selection keys appended. picks: {(file id, index): (order, extra info str)}."""
    got = {}
    for fid, p in enumerate(paths):
        want = {k: v for (f, k), v in picks.items() if f == fid}
        if not want:
            continue
        for k, n, comment, body in _frames(p):
            if k in want:
                order, extra = want[k]
                c = re.sub(r'\s(?:%s)=("[^"]*"|\S+)' % "|".join(SEL_KEYS), "", " " + comment).strip()
                got[order] = b"".join([body[0], (c + " " + extra + "\n").encode()] + body[2:])
    with open(out_path, "wb") as fh:
        for order in sorted(got):
            fh.write(got[order])


# --- per-frame facts -----------------------------------------------------------

def _keys(keys: dict | None) -> dict:
    k = dict(KEYS)
    for name, v in (keys or {}).items():
        if name not in KEYS:
            raise ValueError(f"keys: unknown {name!r}; known: {sorted(KEYS)}")
        k[name] = v
    return k


def facts(pool: dict, keys: dict, need_time: bool) -> dict:
    """Arrays of source, time, volume per frame; refuses missing keys."""
    frames, files = pool["frames"], pool["files"]
    src, t, vol, miss = [], [], [], {}
    for f in frames:
        info = f["info"]
        s = info.get(keys["source"]) if keys["source"] else Path(files[f["file"]]["name"]).name.split(".")[0]
        if s is None:
            miss.setdefault(keys["source"], []).append(f)
        src.append(s)
        tv = info.get(keys["time"]) if keys["time"] else None
        if tv is None and need_time:
            miss.setdefault(keys["time"], []).append(f)
        t.append(float(tv) if tv is not None else math.nan)
        if keys["volume"] and keys["volume"] in info:
            vol.append(float(info[keys["volume"]]))
        elif f["cell"] is not None:
            vol.append(abs(float(np.linalg.det(f["cell"]))))
        else:
            miss.setdefault("Lattice (or a volume key)", []).append(f)
            vol.append(math.nan)
    if miss:
        msg = "; ".join(f"{k!r} missing in {len(v)} of {len(frames)} frames (first: "
                        f"{files[v[0]['file']]['name']} frame {v[0]['index']})" for k, v in miss.items())
        hint = []
        if keys["source"] in miss:
            hint.append("keys={'source': None} names each run after its file")
        if keys["time"] in miss:
            hint.append("min_spacing_ps=0 and no time windows in holdout work without time")
        raise ValueError(f"pool: {msg}" + (f" ({'; '.join(hint)})" if hint else ""))
    return {"source": np.array(src, dtype=object), "time": np.array(t), "volume": np.array(vol)}


def strata_labels(pool: dict, fx: dict, strata: list[str], keys: dict) -> np.ndarray:
    if not strata:
        return np.array(["all"] * len(pool["frames"]), dtype=object)
    out, miss = [], {}
    for i, f in enumerate(pool["frames"]):
        vals = []
        for k in strata:
            v = fx["source"][i] if k in ("source", keys["source"]) else f["info"].get(k)
            if v is None:
                miss.setdefault(k, f)
            vals.append(str(v))
        out.append("|".join(vals))
    if miss:
        raise ValueError("pool: stratum key(s) missing: " + "; ".join(
            f"{k!r} (first: {pool['files'][f['file']]['name']} frame {f['index']})" for k, f in miss.items())
            + "; pass other strata or write the key into the frames")
    return np.array(out, dtype=object)


def holdout_mask(pool: dict, fx: dict, rules: list[dict] | None) -> tuple[np.ndarray, list]:
    n = len(pool["frames"])
    mask, report = np.zeros(n, bool), []
    for r in rules or []:
        bad = set(r) - HOLDOUT_FIELDS
        if bad or not r:
            raise ValueError(f"holdout rule {r}: unknown field(s) {sorted(bad)}; use "
                             f"{sorted(HOLDOUT_FIELDS)}")
        m = np.ones(n, bool)
        if "source" in r:
            m &= np.array([fnmatch.fnmatchcase(str(s), r["source"]) for s in fx["source"]])
        for k, v in (r.get("match") or {}).items():
            m &= np.array([str(f["info"].get(k)) == str(v) for f in pool["frames"]])
        win = [k for k in ("last_ps", "first_ps", "from_ps", "to_ps") if k in r]
        if win:
            if np.isnan(fx["time"][m]).any():
                raise ValueError(f"holdout rule {r}: a time window needs the time key in every frame it covers")
            t = fx["time"]
            w = np.zeros(n, bool)
            for s in set(fx["source"][m]):
                ms = m & (fx["source"] == s)
                tmin, tmax = t[ms].min(), t[ms].max()
                ws = ms.copy()
                if "last_ps" in r:
                    ws &= t >= tmax - float(r["last_ps"]) - 1e-9
                if "first_ps" in r:
                    ws &= t <= tmin + float(r["first_ps"]) + 1e-9
                if "from_ps" in r:
                    ws &= t >= float(r["from_ps"]) - 1e-9
                if "to_ps" in r:
                    ws &= t <= float(r["to_ps"]) + 1e-9
                w |= ws
            m = w
        if not m.any():
            raise ValueError(f"holdout rule {r} matches no frame (sources: "
                             f"{sorted(set(map(str, fx['source'])))[:8]})")
        report.append({"rule": r, "frames": int(m.sum()), "new": int((m & ~mask).sum())})
        mask |= m
    return mask, report


def excluded_mask(pool: dict, fx: dict, shas: set, geoms: list) -> tuple[np.ndarray, dict]:
    frames = pool["frames"]
    by_sha = np.array([f["sha256"] in shas for f in frames], bool)
    by_geo = np.zeros(len(frames), bool)
    unmatched = []
    if geoms:
        order = np.argsort(fx["volume"])
        vs = fx["volume"][order]
        for g in geoms:
            lo = bisect.bisect_left(vs, g["volume"] * (1 - VOL_RTOL))
            hi = bisect.bisect_right(vs, g["volume"] * (1 + VOL_RTOL))
            hit = False
            inv = np.linalg.inv(g["cell"])
            for i in order[lo:hi]:
                f = frames[i]
                if f["natoms"] != g["natoms"] or f["fp"].shape != g["fp"].shape:
                    continue
                d = (f["fp"] - g["fp"]) @ inv
                d = (d - np.round(d)) @ g["cell"]
                if np.abs(d).max() < FP_TOL_A:
                    by_geo[i] = hit = True
            if not hit:
                unmatched.append(g["where"])
    return by_sha | by_geo, {"by_sha256": int(by_sha.sum()), "by_geometry": int((by_geo & ~by_sha).sum()),
                             "shas_given": len(shas), "frames_given": len(geoms),
                             "not_in_pool": len(unmatched), "not_in_pool_first": unmatched[:5]}


# --- strata and picking -------------------------------------------------------------

def _bins(v: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = float(v.min()), float(v.max())
    if k <= 1 or hi - lo < 1e-9:
        return np.zeros(len(v), int), np.array([lo, hi])
    edges = np.linspace(lo, hi, k + 1)
    return np.clip(np.searchsorted(edges, v, side="right") - 1, 0, k - 1), edges


def _spaced(src: np.ndarray, t: np.ndarray, idx: np.ndarray, spacing: float) -> int:
    """How many of idx can be picked >= spacing apart within each source (greedy)."""
    if spacing <= 0:
        return len(idx)
    n = 0
    for s in set(src[idx]):
        last = -math.inf
        for x in np.sort(t[idx][src[idx] == s]):
            if x - last >= spacing - 1e-9:
                n, last = n + 1, x
    return n


class _Spacing:
    """Picked times per source; is a frame >= spacing from all of them?"""

    def __init__(self, src, t, spacing):
        self.src, self.t, self.spacing, self.by = src, t, spacing, {}

    def add(self, i):
        if self.spacing > 0:
            bisect.insort(self.by.setdefault(self.src[i], []), self.t[i])

    def ok(self, i) -> bool:
        if self.spacing <= 0:
            return True
        ts = self.by.get(self.src[i])
        if not ts:
            return True
        j = bisect.bisect_left(ts, self.t[i])
        return all(abs(ts[k] - self.t[i]) >= self.spacing - 1e-9 for k in (j - 1, j) if 0 <= k < len(ts))

    def block(self, i, cand: np.ndarray, valid: np.ndarray) -> None:
        if self.spacing > 0:
            valid &= ~((self.src[cand] == self.src[i]) & (np.abs(self.t[cand] - self.t[i]) < self.spacing - 1e-9))


def features(descriptors: np.ndarray | None, volume: np.ndarray, blocks: list | None) -> np.ndarray:
    """The space FPS measures distance in. Descriptors are centred and each
    element block scaled to unit RMS norm, so the 24 Zr atoms' block counts
    as much as the 200 H atoms' block; without descriptors, the volume."""
    if descriptors is None:
        s = volume.std() or 1.0
        return ((volume - volume.mean()) / s)[:, None]
    x = descriptors.astype(float) - descriptors.mean(axis=0)
    for a, b in blocks or [(0, x.shape[1])]:
        rms = math.sqrt(float((x[:, a:b] ** 2).sum(axis=1).mean()))
        if rms > 0:
            x[:, a:b] /= rms
    return x


def plan(fx, labels, eligible, volume_bins, spacing) -> dict:
    """Per stratum key value: sizes, volume range, bins and spaced capacity."""
    out = {}
    for g in sorted(set(labels)):
        m = labels == g
        e = np.flatnonzero(m & eligible)
        rec = {"frames": int(m.sum()), "eligible": len(e),
               "sources": {str(s): int(((fx["source"] == s) & m).sum()) for s in sorted(set(map(str, fx["source"][m])))}}
        if len(e):
            b, edges = _bins(fx["volume"][e], volume_bins)
            rec["volume_A3"] = [round(float(fx["volume"][e].min()), 2), round(float(fx["volume"][e].max()), 2)]
            rec["spaced_capacity"] = _spaced(fx["source"], fx["time"], e, spacing)
            rec["bins"] = [{"volume_A3": [round(float(edges[k]), 2), round(float(edges[min(k + 1, len(edges) - 1)]), 2)],
                            "eligible": int((b == k).sum()),
                            "spaced_capacity": _spaced(fx["source"], fx["time"], e[b == k], spacing)}
                           for k in range(max(1, volume_bins if len(edges) > 2 else 1))]
        out[g] = rec
    return out


def _allocate(q: int, caps: list[int]) -> list[int]:
    """Split a quota over volume bins as evenly as their capacities allow
    (water-filling); ties go to the bin with more room."""
    alloc = [0] * len(caps)
    left = q
    while left > 0:
        open_ = [k for k in range(len(caps)) if caps[k] > alloc[k]]
        if not open_:
            break
        share, extra = divmod(left, len(open_))
        open_.sort(key=lambda k: (-(caps[k] - alloc[k]), k))
        for r, k in enumerate(open_):
            give = min(caps[k] - alloc[k], share + (1 if r < extra else 0))
            alloc[k] += give
            left -= give
    return alloc


def select(fx: dict, labels: np.ndarray, eligible: np.ndarray, prior: np.ndarray, quotas: dict,
           volume_bins: int, spacing: float, control_fraction: float, seed: int,
           feats: np.ndarray, scores: np.ndarray | None) -> tuple[list, list, dict]:
    """Returns (picks [(pool row, stratum, why)], warnings, per-stratum counts).
    prior: rows already selected earlier that are in the pool (FPS measures
    distance from them; spacing keeps away from them)."""
    rng = np.random.default_rng(seed)
    sp = _Spacing(fx["source"], fx["time"], spacing)
    taken = np.zeros(len(labels), bool)
    sel = [int(i) for i in np.flatnonzero(prior)]
    for i in sel:
        sp.add(i)
    picks, warns, problems, counts = [], [], [], {}
    groups = sorted(set(labels))
    unknown = sorted(set(quotas) - set(groups))
    if unknown:
        raise ValueError(f"quotas name strata not in the pool: {unknown}; the pool has {groups}")
    for g in groups:
        if g not in quotas:
            if (eligible & (labels == g)).any():
                warns.append(f"stratum {g!r} has no quota: none of its frames is picked")
            continue
        q = int(quotas[g])
        if q < 0:
            raise ValueError(f"quota for {g!r} is negative")
        e = np.flatnonzero(eligible & (labels == g))
        cap = _spaced(fx["source"], fx["time"], e, spacing)
        if q > cap:
            problems.append(f"{g!r}: quota {q}, but only {cap} eligible frames are >= {spacing} ps apart "
                            f"({len(e)} eligible)")
    if problems:
        raise ValueError("quotas larger than their strata (nothing written): " + "; ".join(problems)
                         + ". Call without quotas for the strata report.")

    def pick_in(cand: np.ndarray, n: int, g: str, kbin: int) -> int:
        cand = cand[~taken[cand]]
        valid = np.array([sp.ok(i) for i in cand], bool)
        if scores is not None:
            got = 0
            for j in np.argsort(-np.nan_to_num(scores[cand], nan=-np.inf), kind="stable"):
                if got == n:
                    break
                if valid[j] and np.isfinite(scores[cand[j]]):
                    i = int(cand[j])
                    picks.append((i, g, {"by": "score", "score": float(scores[i]), "volume_bin": kbin}))
                    taken[i] = True
                    sp.add(i)
                    sp.block(i, cand, valid)
                    sel.append(i)
                    got += 1
            return got
        x = feats[cand]
        mind = np.full(len(cand), np.inf)
        for i in sel:
            mind = np.minimum(mind, np.linalg.norm(x - feats[i], axis=1))
        got = 0
        while got < n and valid.any():
            if np.isinf(mind[valid]).all():
                j = int(rng.choice(np.flatnonzero(valid)))
                why = {"by": "fps", "first": True}
            else:
                j = int(np.flatnonzero(valid)[np.argmax(mind[valid])])
                why = {"by": "fps", "min_distance": round(float(mind[j]), 6)}
            i = int(cand[j])
            picks.append((i, g, {**why, "volume_bin": kbin}))
            taken[i] = True
            sp.add(i)
            valid[j] = False
            sp.block(i, cand, valid)
            mind = np.minimum(mind, np.linalg.norm(x - feats[i], axis=1))
            sel.append(i)
            got += 1
        return got

    for g in groups:
        q = int(quotas.get(g, 0))
        if q == 0:
            continue
        e = np.flatnonzero(eligible & (labels == g))
        b, _ = _bins(fx["volume"][e], volume_bins)
        nb = int(b.max()) + 1
        caps = [_spaced(fx["source"], fx["time"], e[b == k], spacing) for k in range(nb)]
        alloc = _allocate(q, caps)
        got = [pick_in(e[b == k], alloc[k], g, k) for k in range(nb)]
        short = q - sum(got)
        while short > 0:          # spacing across bins of one run can leave a bin short
            moved = 0
            for k in sorted(range(nb), key=lambda k: (-(caps[k] - got[k]), k)):
                if short and pick_in(e[b == k], 1, g, k):
                    got[k] += 1
                    short -= 1
                    moved += 1
            if not moved:
                break
        if short:
            raise ValueError(f"{g!r}: only {q - short} of {q} frames could be picked >= {spacing} ps apart "
                             "(nothing written); lower the quota or min_spacing_ps")
        counts[g] = {"quota": q, "picked": q, "by_volume_bin": got}

    # control: uniformly spaced over every eligible frame, in pool order
    n_c = int(round(control_fraction * sum(int(v) for v in quotas.values())))
    if n_c:
        e = np.flatnonzero(eligible & ~taken)
        got = 0
        if len(e):
            off = rng.random()
            for k in range(n_c):
                p = min(len(e) - 1, int((k + off) * len(e) / n_c))
                for d in range(len(e)):
                    hit = next((c for c in (p - d, p + d) if 0 <= c < len(e) and not taken[e[c]]
                                and sp.ok(e[c])), None)
                    if hit is not None:
                        i = int(e[hit])
                        picks.append((i, str(labels[i]), {"by": "control", "position": k}))
                        taken[i] = True
                        sp.add(i)
                        got += 1
                        break
        if got < n_c:
            warns.append(f"control: {got} of {n_c} frames found (spacing or a small pool)")
        counts["control"] = {"quota": n_c, "picked": got}
    return picks, warns, counts
