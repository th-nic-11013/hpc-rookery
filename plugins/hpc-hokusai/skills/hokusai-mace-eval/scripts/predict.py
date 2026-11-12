#!/usr/bin/env python3
"""Run one or more MACE models on the frames of a labelled extxyz and write
one predictions file per model: energy, forces and stress per frame, keyed
by each frame's source_sha256 and its atoms (symbols and positions in file
order). hyogo's mlip_compare reads these files against the labels.

Runs inside the MLIP environment (mace-torch, ase, torch), on CPU or one GPU:

    python predict.py LABELS.extxyz --out-dir DIR --dispersion none \\
        --model matpes=/path/MACE-matpes-r2scan-omat-ft.model \\
        --model mp0=/path/2023-12-03-mace-128-L1_epoch-199.model

Writes DIR/<name>.predictions.json for each --model and prints one line per
model. Nothing is subtracted or added: the model's own energy, forces and
stress. --dispersion states what the model's energy contains ("none" for a
model trained on D3-free labels, "included" for one whose labels had D3);
it is recorded, never applied, and mlip_compare refuses a file whose
dispersion differs from the labels'.

From the T1 scratch script eval_models.py (HOKUSAI job 9751103, 2026-11-09).
"""
import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

FORMAT = "mlip-predictions/1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def atoms_sha256(symbols, positions) -> str:
    """The frame's atoms in file order. hyogo/mace/eval.py computes the same
    string from the extxyz text: keep the two in step."""
    text = "".join(f"{s} {x:.5f} {y:.5f} {z:.5f}\n" for s, (x, y, z) in zip(symbols, positions))
    return hashlib.sha256(text.encode()).hexdigest()


def model_e0s(calc, head):
    """{Z: eV} the model subtracts (its atomic_energies), for the head used."""
    try:
        model = calc.models[0]
        zs = [int(z) for z in model.atomic_numbers]
        ae = model.atomic_energies_fn.atomic_energies.detach().cpu().double()
        if ae.dim() == 2:
            heads = list(getattr(model, "heads", ["default"]))
            ae = ae[heads.index(head) if head in heads else 0]
        return {str(z): float(e) for z, e in zip(zs, ae.tolist())}, None
    except Exception as exc:  # noqa: BLE001 - recorded, the predictions still stand
        return None, f"model E0s not read: {exc!r}"


def predict(frames, path, name, head, device, dtype, dispersion, labels, digits):
    import numpy as np
    from mace.calculators import MACECalculator

    kw = {"model_paths": str(path), "device": device, "default_dtype": dtype}
    if head:
        kw["head"] = head
    t0 = time.time()
    calc = MACECalculator(**kw)
    heads = list(getattr(calc.models[0], "heads", []) or [])
    used = head or (heads[0] if heads else None)
    e0s, e0_warn = model_e0s(calc, used)
    out = []
    for i, a in enumerate(frames):
        b = a.copy()
        b.calc = calc
        energy = float(b.get_potential_energy())
        forces = np.asarray(b.get_forces(), dtype=float)
        stress = None
        if all(a.pbc) and a.cell.rank == 3:
            stress = [float(s) for s in b.get_stress(voigt=True)]   # xx yy zz yz xz xy, eV/A^3, ASE sign
        out.append({"index": i,
                    "config_type": a.info.get("config_type"),
                    "source_sha256": a.info.get("source_sha256"),
                    "natoms": len(a),
                    "atoms_sha256": atoms_sha256(a.get_chemical_symbols(), a.get_positions()),
                    "energy": energy,
                    "forces": np.round(forces, digits).tolist(),
                    "stress": stress})
    import ase
    import mace
    import torch
    return {"format": FORMAT,
            "model": {"name": name, "path": str(path), "sha256": sha256_file(path),
                      "head": used, "heads": heads, "dtype": dtype, "device": device,
                      "e0s": e0s},
            "dispersion": dispersion,
            "labels": labels,
            "units": {"energy": "eV", "forces": "eV/A", "stress": "eV/A^3, ASE sign, Voigt xx yy zz yz xz xy"},
            "software": {"mace": mace.__version__, "torch": torch.__version__, "ase": ase.__version__,
                         "python": platform.python_version(), "host": platform.node()},
            "wall_s": round(time.time() - t0, 2),
            "warnings": [w for w in (e0_warn,) if w],
            "frames": out}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("labels", type=Path, help="labelled extxyz (cp2k_make_labels' out)")
    p.add_argument("--model", action="append", required=True, metavar="NAME=PATH",
                   help="a model to run; repeat for several")
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--dispersion", required=True, choices=("none", "included"),
                   help="what the models' energies contain: 'none' (trained on D3-free labels, "
                        "e.g. the MatPES and MP foundation models) or 'included'")
    p.add_argument("--head", default=None, help="model head (default: the model's first)")
    p.add_argument("--device", default="cpu", choices=("cpu", "cuda"))
    p.add_argument("--dtype", default="float64", choices=("float64", "float32"))
    p.add_argument("--digits", type=int, default=7, help="decimals kept on forces (eV/A)")
    p.add_argument("--overwrite", action="store_true")
    args = p.parse_args(argv)

    from ase.io import read

    specs = []
    for spec in args.model:
        name, sep, path = spec.partition("=")
        if not sep or not name or not path:
            p.error(f"--model {spec!r}: expected NAME=PATH")
        path = Path(path).expanduser()
        if not path.is_file():
            p.error(f"--model {name}: no file {path}")
        dest = args.out_dir / f"{name}.predictions.json"
        if dest.exists() and not args.overwrite:
            p.error(f"{dest} exists; pass --overwrite")
        specs.append((name, path, dest))
    if len({n for n, _, _ in specs}) != len(specs):
        p.error("two --model share a name")
    frames = read(str(args.labels), index=":")
    if not frames:
        p.error(f"no frames in {args.labels}")
    labels = {"path": str(args.labels.resolve()), "sha256": sha256_file(args.labels),
              "frames": len(frames)}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, path, dest in specs:
        r = predict(frames, path, name, args.head, args.device, args.dtype, args.dispersion,
                    labels, args.digits)
        dest.write_text(json.dumps(r) + "\n")
        print(f"{name}: {len(frames)} frames, {r['wall_s']} s, head {r['model']['head']} -> {dest}")
        for w in r["warnings"]:
            print(f"{name}: warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
