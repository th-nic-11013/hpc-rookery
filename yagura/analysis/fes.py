"""Free energy along collective variables from a PLUMED COLVAR file, with a
block-averaged error. Numpy only.

Taught from Max Bonomi's do_block_fes.py (PLUMED masterclass 21.4, copied
to tests/fixtures/plumed/master-ISDD-2/): bin the CV values on a grid,
histogram each block of `block_size` consecutive frames (weighted, if
weights are given), average the blocks weighted by their total weight,
and turn the average into F = -kT ln p with the error kT * sigma_p / p.
The file written has the script's format (the grid values, then F and its
error per line, "Inf" for an empty bin, a blank line between rows of a 2D
grid), so it reads the same in gnuplot or a plot script.

Differences from the script: it reads PLUMED's COLVAR directly (columns
picked by name from the "#! FIELDS" line, weights from bias columns as
exp(V/kT)), refuses a point outside the grid on either side (the script
checks only the top, and wraps a point below the bottom), and keeps the
frames beyond the last whole block out with a warning, as the script does
silently.
"""
import math
from pathlib import Path

import numpy as np

KB = {"kj/mol": 0.0083144626, "kcal/mol": 0.0019872043, "ev": 8.617333262e-5}


def read_colvar(path) -> tuple[list[str], np.ndarray, int]:
    """FIELDS names, the data rows, and how many malformed lines were skipped."""
    fields, rows = None, []
    for line in Path(path).read_text().splitlines():
        if line.startswith("#!"):
            w = line.split()
            if w[1:2] == ["FIELDS"]:
                if fields and fields != w[2:]:
                    raise ValueError(f"{path}: FIELDS change part-way (a restarted run "
                                     "with other columns); split the file first")
                fields = w[2:]
            continue
        if not line.strip() or line.startswith("#"):
            continue
        rows.append(line.split())
    if fields is None:
        raise ValueError(f"{path} has no '#! FIELDS' line: is it a PLUMED COLVAR file?")
    n = len(fields)
    good = [r for r in rows if len(r) == n]
    data = np.array(good, float) if good else np.zeros((0, n))
    return fields, data, len(rows) - len(good)


def fes(path, cvs: list[str], grid: list[list[float]], temperature: float,
        block_size: int, units: str = "kj/mol", bias: list[str] | None = None,
        frames: str = ":") -> tuple[str, dict]:
    if units.lower() not in KB:
        raise ValueError(f"units is one of {sorted(KB)} (PLUMED's default is kj/mol)")
    kbt = KB[units.lower()] * temperature
    if not 1 <= len(cvs) <= 3:
        raise ValueError("cvs: one to three CV names from the COLVAR FIELDS line")
    if len(grid) != len(cvs) or any(len(g) != 3 for g in grid):
        raise ValueError("grid: one [min, max, nbins] per CV, e.g. [[-3.141593, 3.141593, 50]]")
    fields, data, skipped = read_colvar(path)
    missing = [c for c in cvs + (bias or []) if c not in fields]
    if missing:
        raise ValueError(f"{missing} not in the COLVAR fields {fields}")
    parts = [int(p) if p.strip() else None for p in frames.split(":")]
    if len(parts) not in (2, 3):
        raise ValueError(f"frames is START:STOP[:STEP]; got {frames!r}")
    data = data[slice(*parts)]
    warnings = []
    if skipped:
        warnings.append(f"{skipped} malformed line(s) skipped (a run killed mid-write?)")
    gmin = [float(g[0]) for g in grid]
    nbin = [int(g[2]) for g in grid]
    if any(n < 2 for n in nbin) or any(g[1] <= g[0] for g in grid):
        raise ValueError("each grid needs max > min and at least 2 bins")
    dx = [(float(g[1]) - float(g[0])) / (n - 1) for g, n in zip(grid, nbin)]
    x = data[:, [fields.index(c) for c in cvs]]
    if bias:
        v = data[:, [fields.index(b) for b in bias]].sum(axis=1) / kbt
        w = np.exp(v - v.max())   # a constant factor cancels in each block's normalisation
    else:
        w = np.ones(len(x))
    idx = np.rint((x - np.array(gmin)) / np.array(dx)).astype(int)
    out = (idx < 0) | (idx >= np.array(nbin))
    if out.any():
        i = int(np.argmax(out.any(axis=1)))
        raise ValueError(f"{int(out.any(axis=1).sum())} frame(s) fall outside the grid (first: "
                         f"{dict(zip(cvs, x[i].tolist()))}); widen grid. Grid points sit at "
                         "min + k*(max-min)/(nbins-1), each bin half a spacing either side")
    flat = idx[:, -1]
    for j in range(len(nbin) - 2, -1, -1):
        flat = flat * nbin[j] + idx[:, j]
    nbins = int(np.prod(nbin))
    nblock = len(x) // block_size
    if nblock < 2:
        raise ValueError(f"{len(x)} frames make {nblock} block(s) of {block_size}; need at "
                         "least 2 (5+ for a meaningful error): use a smaller block_size")
    if nblock < 5:
        warnings.append(f"only {nblock} blocks: the error estimate is rough")
    if len(x) % block_size:
        warnings.append(f"the last {len(x) % block_size} frame(s) don't fill a block and are left out")
    histo = np.zeros((nbins, nblock))
    norm = np.zeros(nblock)
    for b in range(nblock):
        s = slice(b * block_size, (b + 1) * block_size)
        histo[:, b] = np.bincount(flat[s], weights=w[s], minlength=nbins)
        norm[b] = w[s].sum()
        histo[:, b] /= norm[b]
    ave = np.sum(histo * norm, axis=1) / np.sum(norm)
    var = np.sum(np.power(norm * (histo - ave[:, None]), 2), axis=1) / np.power(np.sum(norm), 2)
    neff = float(norm.sum() ** 2 / (norm ** 2).sum())
    if bias and neff < 0.5 * nblock:
        warnings.append(f"the bias weights make {neff:.1f} effective blocks of {nblock}: a few "
                        "frames dominate, so the error is underestimated")

    lines, prev = [], None
    fvals, evals = np.full(nbins, np.inf), np.full(nbins, np.inf)
    for i in range(nbins):
        k, ids = i, []
        for n in nbin:
            ids.append(k % n)
            k //= n
        xs = [gmin[j] + float(ids[j]) * dx[j] for j in range(len(nbin))]
        if prev is not None and xs[1:] != prev[1:]:
            lines.append("")
        prev = xs
        head = "".join("%12.6f " % v for v in xs)
        if ave[i] > 0:
            fvals[i] = -kbt * math.log(ave[i])
            evals[i] = math.sqrt((kbt / ave[i]) ** 2 * var[i])
            lines.append(head + "   %12.6f %12.6f" % (fvals[i], evals[i]))
        else:
            lines.append(head + "   %12s %12s" % ("Inf", "Inf"))
    text = "\n".join(lines) + "\n"
    finite = np.isfinite(fvals)
    if not finite.any():
        raise ValueError("every bin is empty")
    imin = int(np.argmin(np.where(finite, fvals, np.inf)))
    k, ids = imin, []
    for n in nbin:
        ids.append(k % n)
        k //= n
    empty = int((~finite).sum())
    if empty:
        warnings.append(f"{empty} of {nbins} bins are empty (F = Inf): unsampled, not high")
    return text, {
        "cvs": cvs, "frames_used": nblock * block_size, "nblocks": nblock, "block_size": block_size,
        "kbt": round(kbt, 6), "units": units.lower(), "weights": "bias" if bias else "uniform",
        "effective_blocks": round(neff, 2),
        "minimum": {"at": {c: round(gmin[j] + ids[j] * dx[j], 6) for j, c in enumerate(cvs)},
                    "error": round(float(evals[imin]), 6)},
        "max_error": round(float(evals[finite].max()), 6),
        "mean_error": round(float(evals[finite].mean()), 6),
        "empty_bins": empty, "warnings": warnings}
