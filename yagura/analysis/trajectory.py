"""Analyse an MD trajectory: RDF, MSD and diffusion, VDOS. Numpy only.

Usage (light enough for the front end; run it in a job for long trajectories):
    {{FRONT_END_PYTHON}} -I trajectory.py rdf  TRAJ.xyz --pairs O-H O-O [--r-max 6 --bins 120]
    {{FRONT_END_PYTHON}} -I trajectory.py msd  TRAJ.xyz --elements O --dt-fs 0.5 [--fit-ps 1 5]
    {{FRONT_END_PYTHON}} -I trajectory.py vdos TRAJ.xyz --dt-fs 0.5 [--elements H]
each with [--cell 12.4 12.4 12.4 | --cell ax ay az bx by bz cx cy cz] [--frames START:STOP:STEP].
Prints one JSON object, the same as yagura's trajectory_rdf / _msd / _vdos tools.

Input is multi-frame xyz: extended xyz with a Lattice="..." line per frame,
or CP2K's PROJECT-pos-1.xyz, which has no cell, so pass --cell. Every frame
must list the same atoms in the same order. Units: positions and cell in A,
dt_fs (the time between consecutive frames in the file: CP2K's TIMESTEP
times MOTION/PRINT/TRAJECTORY/EACH/MD) in fs.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

C_CM_PER_FS = 2.99792458e-5   # speed of light: wavenumber (cm^-1) = frequency (1/fs) / c
MAX_BINS = 1000


def _slice(frames: str) -> slice:
    parts = frames.split(":")
    if len(parts) > 3 or not all(re.fullmatch(r"\s*-?\d*\s*", p) for p in parts):
        raise ValueError(f"frames is START:STOP:STEP like '::10' or '100:'; got {frames!r}")
    vals = [int(p) if p.strip() else None for p in parts]
    if len(vals) == 1:
        raise ValueError(f"frames is a slice START:STOP:STEP, not one frame; got {frames!r}")
    if len(vals) == 3 and vals[2] is not None and vals[2] <= 0:
        raise ValueError("the frames step must be positive")
    return slice(*vals)


def _cell(cell) -> np.ndarray:
    a = np.asarray(cell, float).ravel()
    if a.size == 3:
        return np.diag(a)
    if a.size == 9:
        return a.reshape(3, 3)
    raise ValueError("cell is 3 lengths (orthorhombic) or a 3x3 matrix of cell vectors as rows, in A")


def read(path, cell=None, frames: str = ":") -> dict:
    """The selected frames: elements, positions (F, N, 3) in A, cells
    (F, 3, 3), each frame's time = stamp in fs if every comment has one."""
    lines = Path(path).read_text().splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    try:
        n = int(lines[0])
    except (IndexError, ValueError):
        raise ValueError(f"{path} is not an xyz file: line 1 should be the atom count") from None
    block = n + 2
    if len(lines) % block:
        raise ValueError(f"{len(lines)} lines is not a whole number of {n}-atom frames: every "
                         "frame must have the same atoms (no variable atom count)")
    step = _slice(frames).indices(len(lines) // block)[2]
    idx = list(range(len(lines) // block))[_slice(frames)]
    if not idx:
        raise ValueError(f"frames {frames!r} selects none of the {len(lines) // block} frames")
    el = np.array([l.split()[0] for l in lines[2:2 + n]])
    for k in (idx[0], idx[-1]):
        if lines[k * block].strip() != str(n) or \
                [l.split()[0] for l in lines[k * block + 2:k * block + 2 + n]] != list(el):
            raise ValueError(f"frame {k} does not list the same {n} atoms in the same order as frame 0")
    comments = [lines[k * block + 1] for k in idx]
    X = np.loadtxt([l for k in idx for l in lines[k * block + 2:k * block + 2 + n]],
                   usecols=(1, 2, 3), ndmin=2).reshape(len(idx), n, 3)

    lattice = [re.search(r'Lattice="([^"]+)"', c) for c in comments]
    if cell is not None:
        cells, source = np.repeat(_cell(cell)[None], len(idx), axis=0), "argument"
    elif all(lattice):
        cells, source = np.array([np.array(m.group(1).split(), float).reshape(3, 3)
                                  for m in lattice]), "Lattice= lines"
    else:
        raise ValueError("the frames have no Lattice=\"...\" line (CP2K's -pos-1.xyz has none), "
                         "and the analysis needs the cell for periodic images: pass cell, as 3 "
                         "lengths in A or a 3x3 matrix of cell vectors as rows (CP2K: &CELL ABC)")
    if np.any(np.abs(np.linalg.det(cells)) < 1e-6):
        raise ValueError("the cell has zero volume")
    stamps = [re.search(r"\btime\s*=\s*([-+0-9.eE]+)", c) for c in comments]
    times = np.array([float(m.group(1)) for m in stamps]) if all(stamps) else None
    return {"elements": el, "X": X, "cells": cells, "cell_source": source, "times": times,
            "step": step, "frames_used": len(idx), "frames_in_file": len(lines) // block}


def _select(el, elements):
    if not elements:
        return np.ones(len(el), bool)
    missing = sorted(set(elements) - set(el))
    if missing:
        raise ValueError(f"no {missing} atoms in the trajectory; it has {sorted(set(el))}")
    return np.isin(el, elements)


def _widths(cells):
    vol = np.abs(np.linalg.det(cells))
    return np.stack([vol / np.linalg.norm(np.cross(cells[:, (i + 1) % 3], cells[:, (i + 2) % 3]),
                                          axis=1) for i in range(3)], axis=1)


def _unwrap(X, cells, warnings):
    """Positions made continuous across periodic boundaries: each step
    between frames is taken as the minimum image, so atoms must move less
    than half a cell width between frames (always true for saved MD frames)."""
    F = np.einsum("fni,fij->fnj", X, np.linalg.inv(cells))
    dF = np.diff(F, axis=0)
    dF -= np.round(dF)
    if dF.size and np.abs(dF).max() > 0.3:
        warnings.append(f"an atom moved {np.abs(dF).max():.2f} of a cell vector between frames; "
                        "unwrapping assumes much less (frames too far apart?)")
    dX = np.einsum("fni,fij->fnj", dF, cells[1:])
    return np.concatenate([X[:1], X[:1] + np.cumsum(dX, axis=0)])


def _check_dt(t, dt, warnings):
    if t["times"] is not None and len(t["times"]) > 1:
        seen = float(np.median(np.diff(t["times"])))
        if abs(seen - dt) > 0.01 * abs(dt):
            warnings.append(f"the file's time = stamps are {seen:g} fs apart, but dt_fs x the frames "
                            f"step is {dt:g} fs: check dt_fs (the time between frames in the file)")


def _peaks(x, y, k, floor=0.0):
    """The k highest local maxima of y above floor, by x, interpolated
    with a parabola through each maximum and its neighbours."""
    i = np.where((y[1:-1] > y[:-2]) & (y[1:-1] >= y[2:]) & (y[1:-1] > floor))[0] + 1
    i = i[np.argsort(y[i])[::-1][:k]]
    out = []
    for j in sorted(i):
        a, b, c = y[j - 1], y[j], y[j + 1]
        den = a - 2 * b + c
        s = 0.5 * (a - c) / den if den else 0.0
        out.append((float(x[j] + s * (x[1] - x[0])), float(b - 0.25 * (a - c) * s), int(j)))
    return out


def rdf(path, pairs, r_max: float | None = None, bins: int = 120, cell=None, frames: str = ":") -> dict:
    """g(r) per element pair, minimum image, averaged over the frames."""
    t = read(path, cell, frames)
    el, X, cells = t["elements"], t["X"], t["cells"]
    if not 1 <= bins <= MAX_BINS:
        raise ValueError(f"bins is between 1 and {MAX_BINS}")
    half = float(_widths(cells).min()) / 2
    if r_max is None:
        r_max = float(np.floor(half * 100) / 100)
    if not 0 < r_max <= half + 1e-9:
        raise ValueError(f"r_max must be above 0 and at most half the smallest perpendicular cell "
                         f"width, {half:.3f} A, or the minimum image misses pairs")
    edges = np.linspace(0.0, r_max, bins + 1)
    r = 0.5 * (edges[1:] + edges[:-1])
    shell = 4 / 3 * np.pi * (edges[1:] ** 3 - edges[:-1] ** 3)
    vol = np.abs(np.linalg.det(cells))
    inv = np.linalg.inv(cells)
    out = []
    for p in pairs:
        a, _, b = p.partition("-")
        if not a or not b:
            raise ValueError(f"pairs are element pairs like 'O-H'; got {p!r}")
        ia, ib = np.where(_select(el, [a]))[0], np.where(_select(el, [b]))[0]
        npairs = len(ia) * (len(ib) - 1 if a == b else len(ib))
        if not npairs:
            raise ValueError(f"{p}: only one {a} atom, so no pairs")
        g, counts = np.zeros(bins), np.zeros(bins)
        chunk = max(1, 2_000_000 // len(ib))
        for f in range(len(X)):
            Fa, Fb = X[f, ia] @ inv[f], X[f, ib] @ inv[f]
            h = np.zeros(bins)
            for s in range(0, len(ia), chunk):
                d = Fa[s:s + chunk, None, :] - Fb[None, :, :]
                d -= np.round(d)
                dist = np.linalg.norm(d @ cells[f], axis=2)
                if a == b:
                    dist[ia[s:s + chunk, None] == ib[None, :]] = np.inf
                h += np.histogram(dist, edges)[0]
            counts += h
            g += h * vol[f] / npairs
        g /= len(X) * shell
        n_r = np.cumsum(counts) / (len(ia) * len(X))   # mean number of b within r of an a
        smooth = np.convolve(np.pad(g, 1, mode="edge"), np.ones(3) / 3, mode="valid")
        peaks = _peaks(r, smooth, 3, floor=1.0)
        first_min = None
        if peaks:
            after = np.where((smooth[1:-1] < smooth[:-2]) & (smooth[1:-1] <= smooth[2:]))[0] + 1
            after = after[after > peaks[0][2]]
            if len(after):
                j = int(after[0])
                first_min = {"r": round(float(r[j]), 3), "g": round(float(g[j]), 3),
                             "coordination": round(float(n_r[j]), 3)}
        out.append({"pair": p, "n_a": len(ia), "n_b": len(ib),
                    "g": [round(float(v), 4) for v in g],
                    "peaks": [{"r": round(x, 3), "g": round(y, 3)} for x, y, _ in peaks],
                    "first_min": first_min})
    return {"units": {"r": "A", "g": "dimensionless (1 = uncorrelated)",
                      "coordination": "mean number of B atoms within r of an A atom"},
            "r": [round(float(v), 4) for v in r], "pairs": out, "r_max": r_max, "bins": bins,
            "frames_used": t["frames_used"], "frames_in_file": t["frames_in_file"],
            "cell_source": t["cell_source"], "warnings": []}


def _msd_fft(U):
    """MSD over every time origin, averaged over atoms, for lags 0..F-1
    (U is (F, n, 3), unwrapped); O(F log F) per atom."""
    F, n, _ = U.shape
    D = np.concatenate([(U ** 2).sum(axis=2), np.zeros((1, n))])
    Q = 2 * D.sum(axis=0)
    S1 = np.empty((F, n))
    for m in range(F):
        Q = Q - D[m - 1] - D[F - m]
        S1[m] = Q / (F - m)
    f = np.fft.rfft(U, n=2 * F, axis=0)
    S2 = np.fft.irfft((f * f.conj()).real.sum(axis=2), n=2 * F, axis=0)[:F] / (F - np.arange(F))[:, None]
    return (S1 - 2 * S2).mean(axis=1)


def msd(path, elements, dt_fs: float, cell=None, frames: str = ":", fit_ps=None,
        points: int = 100, remove_drift: bool = True) -> dict:
    """MSD per element over lags up to half the trajectory, D from a
    linear fit, and the log-log slope over the same window."""
    if dt_fs <= 0:
        raise ValueError("dt_fs is the time between frames in the file, in fs, above 0")
    if not elements:
        raise ValueError("elements: name the elements to follow, e.g. ['O']")
    t = read(path, cell, frames)
    warnings = []
    dt_ps = dt_fs * t["step"] / 1000
    _check_dt(t, dt_fs * t["step"], warnings)
    if t["frames_used"] < 20:
        raise ValueError(f"{t['frames_used']} frames is too few for an MSD; need at least 20")
    U = _unwrap(t["X"], t["cells"], warnings)
    if remove_drift:
        U = U - (U.mean(axis=1, keepdims=True) - U[:1].mean(axis=1, keepdims=True))
    max_lag = t["frames_used"] // 2
    lag_t = np.arange(max_lag + 1) * dt_ps
    if fit_ps is None:
        lo, hi = 0.2 * lag_t[-1], 0.8 * lag_t[-1]
    else:
        lo, hi = map(float, fit_ps)
    win = (lag_t >= lo - 1e-12) & (lag_t <= hi + 1e-12) & (lag_t > 0)
    if win.sum() < 3 or hi > lag_t[-1] + 1e-9:
        raise ValueError(f"fit_ps [{lo:g}, {hi:g}] must hold at least 3 lags within 0 to "
                         f"{lag_t[-1]:g} ps (half the trajectory)")
    keep = np.unique(np.round(np.linspace(0, max_lag, min(points, max_lag + 1))).astype(int))
    out = []
    for e in elements:
        m = _msd_fft(U[:, _select(t["elements"], [e])])[:max_lag + 1]
        slope, icpt = np.polyfit(lag_t[win], m[win], 1)
        resid = m[win] - (slope * lag_t[win] + icpt)
        r2 = 1 - (resid ** 2).sum() / max(((m[win] - m[win].mean()) ** 2).sum(), 1e-300)
        pos = win & (m > 0)
        alpha = float(np.polyfit(np.log(lag_t[pos]), np.log(m[pos]), 1)[0]) if pos.sum() >= 2 else None
        D = slope / 6
        out.append({"element": e, "natoms": int((t["elements"] == e).sum()),
                    "msd": [round(float(v), 5) for v in m[keep]],
                    "D_A2_per_ps": round(float(D), 6), "D_cm2_per_s": float(f"{D * 1e-4:.4g}"),
                    "fit": {"slope_A2_per_ps": round(float(slope), 6),
                            "intercept_A2": round(float(icpt), 5), "r2": round(float(r2), 4)},
                    "loglog_slope": None if alpha is None else round(alpha, 3)})
    return {"units": {"t": "ps", "msd": "A^2", "D": "A^2/ps (1 A^2/ps = 1e-4 cm^2/s)"},
            "t_ps": [round(float(v), 6) for v in lag_t[keep]], "elements": out,
            "fit_window_ps": [round(lo, 6), round(hi, 6)], "dt_ps": dt_ps,
            "method": "unwrapped minimum-image steps, every time origin, lags up to half the "
                      "trajectory; D = slope/6 of a straight line over fit_window_ps; "
                      "loglog_slope near 1 is diffusive, near 2 ballistic, below 1 caged",
            "drift": "removed (unweighted mean of all atoms)" if remove_drift else "kept",
            "frames_used": t["frames_used"], "frames_in_file": t["frames_in_file"],
            "cell_source": t["cell_source"], "warnings": warnings}


def vdos(path, dt_fs: float, elements=None, cell=None, frames: str = ":",
         max_wavenumber: float = 4000.0, points: int = 400, n_peaks: int = 5) -> dict:
    """VDOS: the cosine transform of the velocity autocorrelation, with
    velocities differenced from unwrapped positions."""
    if dt_fs <= 0:
        raise ValueError("dt_fs is the time between frames in the file, in fs, above 0")
    t = read(path, cell, frames)
    warnings = []
    dt = dt_fs * t["step"]
    _check_dt(t, dt, warnings)
    if t["frames_used"] < 32:
        raise ValueError(f"{t['frames_used']} frames is too few for a VDOS; need at least 32")
    U = _unwrap(t["X"], t["cells"], warnings)[:, _select(t["elements"], elements)]
    V = (U[2:] - U[:-2]) / (2 * dt)
    N, M = len(V), len(V) // 2
    f = np.fft.rfft(V, n=2 * N, axis=0)
    c = np.fft.irfft((f * f.conj()).real.sum(axis=2), n=2 * N, axis=0)[:M].mean(axis=1)
    c /= N - np.arange(M)
    cw = c * np.cos(0.5 * np.pi * np.arange(M) / M) ** 2   # half-Hann taper to zero at lag M
    S = np.fft.rfft(np.concatenate([cw, cw[:0:-1]])).real
    wn = np.fft.rfftfreq(2 * M - 1, dt) / C_CM_PER_FS
    nyquist = 1 / (2 * dt) / C_CM_PER_FS
    if max_wavenumber > nyquist:
        warnings.append(f"max_wavenumber {max_wavenumber:g} is above the Nyquist limit of "
                        f"{nyquist:.0f} cm^-1 for {dt:g} fs frames; cut there")
        max_wavenumber = nyquist
    inside = wn <= max_wavenumber
    wn, S = wn[inside], S[inside]
    area = np.trapezoid(S, wn) if hasattr(np, "trapezoid") else np.trapz(S, wn)
    S = S / area if area > 0 else S
    edges = np.linspace(0.0, max_wavenumber, points + 1)
    centres = 0.5 * (edges[1:] + edges[:-1])
    res = wn[1] - wn[0]
    if res < edges[1] - edges[0]:
        k = np.clip(np.digitize(wn, edges) - 1, 0, points - 1)
        S_out = np.bincount(k, S, points) / np.maximum(np.bincount(k, minlength=points), 1)
    else:
        S_out = np.interp(centres, wn, S)
    peaks = _peaks(wn, S, n_peaks)
    top = max([y for _, y, _ in peaks], default=1.0)
    peaks = [p for p in peaks if p[1] >= 0.05 * top]
    x = 2 * np.pi * C_CM_PER_FS * max_wavenumber * dt
    damp = (np.sin(x) / x) ** 2
    return {"units": {"wavenumber": "cm^-1", "vdos": "normalised to unit area over 0 to "
                      "max_wavenumber (units: cm)"},
            "wavenumber": [round(float(v), 2) for v in centres],
            "vdos": [float(f"{v:.5g}") for v in S_out],
            "peaks": [{"wavenumber": round(x_, 1), "relative": round(y / top, 3)} for x_, y, _ in peaks],
            "resolution_cm-1": round(float(res), 2), "max_wavenumber": round(float(max_wavenumber), 2),
            "elements": sorted(set(t["elements"][_select(t["elements"], elements)])),
            "natoms": int(U.shape[1]),
            "velocities": f"central differences of positions, (x[t+dt] - x[t-dt]) / (2 dt), not "
                          f"the MD velocities: peak positions are right, intensities fall at high "
                          f"wavenumber (power x{damp:.3f} at {max_wavenumber:.0f} cm^-1 for "
                          f"{dt:g} fs frames). Unweighted by mass.",
            "method": "velocity autocorrelation over every origin, lags up to half the "
                      "trajectory, half-Hann taper, cosine transform",
            "frames_used": t["frames_used"], "frames_in_file": t["frames_in_file"],
            "cell_source": t["cell_source"], "warnings": warnings}


def main(argv):
    ap = argparse.ArgumentParser(prog="trajectory.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="what", required=True)
    for name in ("rdf", "msd", "vdos"):
        s = sub.add_parser(name)
        s.add_argument("path")
        s.add_argument("--cell", type=float, nargs="+")
        s.add_argument("--frames", default=":")
        if name == "rdf":
            s.add_argument("--pairs", nargs="+", required=True)
            s.add_argument("--r-max", type=float)
            s.add_argument("--bins", type=int, default=120)
        else:
            s.add_argument("--dt-fs", type=float, required=True)
            s.add_argument("--elements", nargs="+", required=name == "msd")
        if name == "msd":
            s.add_argument("--fit-ps", type=float, nargs=2)
        if name == "vdos":
            s.add_argument("--max-wavenumber", type=float, default=4000.0)
    a = ap.parse_args(argv)
    try:
        if a.what == "rdf":
            r = rdf(a.path, a.pairs, a.r_max, a.bins, a.cell, a.frames)
        elif a.what == "msd":
            r = msd(a.path, a.elements, a.dt_fs, a.cell, a.frames, a.fit_ps)
        else:
            r = vdos(a.path, a.dt_fs, a.elements, a.cell, a.frames, a.max_wavenumber)
    except ValueError as e:
        sys.exit(str(e))
    print(json.dumps(r))


if __name__ == "__main__":
    main(sys.argv[1:])
