"""Check an exported metatomic model (.pt) against the same model's float64
predictions on a few labelled frames, before any MD runs on it.

Float32 shifts every frame's energy by a near-constant offset, so the gate is
the offset's spread across frames and the largest force difference, not the
offset itself (UiO-66 round 3: offset 0.45 meV/atom, spread 0.008, max dF
0.12 meV/A). Exit 1 when the gate fails.

usage: python check_export.py --frames labels.extxyz --ref model.predictions.json
           --pt exported.pt [--single single-head.model] [--n 3]
           [--max-spread 0.1] [--max-df 5]
  --ref: hyogo/mace-eval predict.py output for the source model on --frames
  --single: also check the single-head .model (MACECalculator, float32)
"""
import argparse
import json
import sys

import ase.io
import numpy as np

p = argparse.ArgumentParser()
p.add_argument("--frames", required=True)
p.add_argument("--ref", required=True)
p.add_argument("--pt", required=True)
p.add_argument("--single")
p.add_argument("--n", type=int, default=3)
p.add_argument("--max-spread", type=float, default=0.1, help="meV/atom")
p.add_argument("--max-df", type=float, default=5.0, help="meV/A")
a = p.parse_args()

frames = ase.io.read(a.frames, f":{a.n}")
ref = json.load(open(a.ref))["frames"][:a.n]


def compare(name, calc):
    dE, dF = [], []
    for at, r in zip(frames, ref):
        b = at.copy()
        b.calc = calc
        dE.append((b.get_potential_energy() - r["energy"]) / len(b) * 1e3)
        dF.append(np.abs(b.get_forces() - np.asarray(r["forces"])).max() * 1e3)
    spread = max(dE) - min(dE)
    print(json.dumps({"model": name, "dE_meV_per_atom": np.round(dE, 4).tolist(),
                      "spread_meV_per_atom": round(spread, 4), "max_dF_meV_per_A": round(max(dF), 4)}))
    return spread, max(dF)


if a.single:
    from mace.calculators import MACECalculator
    compare(a.single, MACECalculator(model_paths=a.single, device="cpu", default_dtype="float32"))
try:
    from metatomic.torch.ase_calculator import MetatomicCalculator
except ImportError:
    from metatomic_ase import MetatomicCalculator
spread, df = compare(a.pt, MetatomicCalculator(a.pt, device="cpu"))
ok = spread < a.max_spread and df < a.max_df
print("PASS" if ok else f"FAIL: spread {spread:.4f} (< {a.max_spread}) and max dF {df:.4f} (< {a.max_df}) needed")
sys.exit(0 if ok else 1)
