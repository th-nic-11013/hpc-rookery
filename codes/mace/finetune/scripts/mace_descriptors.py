#!/usr/bin/env python3
"""Per-frame descriptors of a pool of frames, for hyogo's mlip_select_frames.

For every frame of the given extxyz files (in order), the MACE model's
invariant node features (all layers) are averaged over the atoms of each
element and the per-element means are concatenated: one row per frame,
written to OUT.npy (float32), plus OUT.json naming the pool files (sha256,
frame count), the model (path, sha256), the element blocks and the run.
mlip_select_frames checks that json against the pool it is given.

Why per element, not one mean over all atoms: in UiO-66 (456 atoms: 24 Zr,
192 C, 128 O, 112 H) a pooled mean is 2/3 C and H, so a change at the
Zr-O nodes (the excursions that matter) moves it by a few percent; per
element, the Zr and O blocks carry it in full, and the selector scales
each block to the same weight. An element missing from a frame gets zeros
(UiO-66 pools have one composition, so that does not happen there).

Runs where mace-torch is installed (the cluster's MLIP environment), on a
GPU or CPU:

    python mace_descriptors.py --model MODEL --out desc.npy pool1.extxyz [pool2.extxyz ...]
    python mace_descriptors.py --dry-run --out desc.npy pool*.extxyz   # count and hash only

The pool files must be the ones given to mlip_select_frames, in the same
order (same bytes; .gz is fine).
"""
import argparse
import gzip
import hashlib
import json
import sys
import time
from pathlib import Path


def _open(p: Path):
    return gzip.open(p, "rb") if p.suffix == ".gz" else open(p, "rb")


def count_frames(p: Path) -> int:
    """Frames as mlip_select_frames counts them (blank lines between frames skipped)."""
    n = 0
    with _open(p) as fh:
        for line in fh:
            if not line.strip():
                continue
            k = int(line.split()[0])
            for _ in range(k + 1):
                if not fh.readline():
                    raise SystemExit(f"{p}: frame {n} is cut short")
            n += 1
    return n


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pool", nargs="+", type=Path)
    ap.add_argument("--out", required=True, type=Path, help="OUT.npy (OUT.json is written beside it)")
    ap.add_argument("--model", help="MACE model file (the base model of the fine-tune)")
    ap.add_argument("--device", default="cuda", help="cuda or cpu (default cuda)")
    ap.add_argument("--dtype", default="float64", help="the model's precision (float64 for MATPES / MP-0)")
    ap.add_argument("--elements", help="element order, comma separated (default: the first frame's, by Z)")
    ap.add_argument("--dry-run", action="store_true", help="count frames and hash files; no model")
    a = ap.parse_args()
    if a.out.suffix != ".npy":
        raise SystemExit("--out must end in .npy")
    t0 = time.time()
    files = []
    for p in a.pool:
        if not p.is_file():
            raise SystemExit(f"{p}: not a file")
        files.append({"name": p.name, "path": str(p.resolve()), "sha256": sha256(p), "frames": count_frames(p)})
        print(f"{p.name}: {files[-1]['frames']} frames", file=sys.stderr)
    n = sum(f["frames"] for f in files)
    meta = {"script": "mace_descriptors.py", "pool": files, "frames": n,
            "layout": "per-element mean of invariant node features, all layers, elements concatenated",
            "absent_element": "zeros"}
    if a.dry_run:
        print(json.dumps(meta, indent=1))
        return 0
    if not a.model:
        raise SystemExit("--model is needed (or --dry-run)")

    import numpy as np
    from ase.data import atomic_numbers
    from ase.io import iread
    import mace
    from mace.calculators import MACECalculator

    calc = MACECalculator(model_paths=a.model, device=a.device, default_dtype=a.dtype)
    elements = a.elements.split(",") if a.elements else None
    arr, k, dim = None, 0, None
    t1 = time.time()
    for p in a.pool:
        for atoms in iread(str(p), index=":", format="extxyz"):
            sym = np.array(atoms.get_chemical_symbols())
            if elements is None:
                elements = sorted(set(sym), key=atomic_numbers.get)
            extra = set(sym) - set(elements)
            if extra:
                raise SystemExit(f"{p.name} frame {k}: elements {sorted(extra)} not in {elements}")
            d = calc.get_descriptors(atoms, invariants_only=True)      # (atoms, layers x invariants)
            if arr is None:
                dim = d.shape[1]
                arr = np.lib.format.open_memmap(a.out, mode="w+", dtype=np.float32,
                                                shape=(n, dim * len(elements)))
            row = np.zeros(dim * len(elements))
            for j, el in enumerate(elements):
                m = sym == el
                if m.any():
                    row[j * dim:(j + 1) * dim] = d[m].mean(axis=0)
            arr[k] = row
            k += 1
            if k % 1000 == 0:
                rate = (time.time() - t1) / k
                print(f"{k}/{n} frames, {rate:.3f} s/frame, ~{rate * (n - k) / 60:.0f} min left",
                      file=sys.stderr, flush=True)
    if k != n:
        raise SystemExit(f"read {k} frames with ASE but counted {n}: the files changed or are not plain extxyz")
    arr.flush()
    meta.update({"model": str(Path(a.model).resolve()), "model_sha256": sha256(Path(a.model)),
                 "mace_version": getattr(mace, "__version__", None), "device": a.device, "dtype": a.dtype,
                 "elements": elements, "dim_per_element": dim,
                 "blocks": {el: [j * dim, (j + 1) * dim] for j, el in enumerate(elements)},
                 "shape": [n, dim * len(elements)], "wall_s": round(time.time() - t0, 1),
                 "s_per_frame": round((time.time() - t1) / max(n, 1), 4)})
    a.out.with_suffix(".json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"wrote {a.out} {meta['shape']} and {a.out.with_suffix('.json').name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
