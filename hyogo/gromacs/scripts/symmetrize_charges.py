#!/usr/bin/env python3
"""Make TFSI/MeOH GAFF templates symmetric: one GAFF type per equivalent role, charges averaged over
equivalent atoms (net charge restored), then TFSI charges scaled by --scale (Li is scaled in to_gromacs.py).
Antechamber types the two TFSI S atoms differently (s6/sy) and gives unequal O charges, hence this step."""
import argparse
import re

ap = argparse.ArgumentParser()
ap.add_argument("--scale", type=float, default=0.8)
SCALE = ap.parse_args().scale

def process(inp, outp, classes, types, net, scale):
    lines = open(inp).read().split("\n")
    a0 = lines.index("@<TRIPOS>ATOM") + 1
    a1 = lines.index("@<TRIPOS>BOND")
    atoms = [lines[k].split() for k in range(a0, a1)]
    groups = {}
    for idx, f in enumerate(atoms):
        groups.setdefault(classes(f[1]), []).append(idx)
    q = [float(f[8]) for f in atoms]
    for g, ids in groups.items():
        m = sum(q[i] for i in ids) / len(ids)
        for i in ids:
            q[i] = m
    resid = (net - sum(q)) / len(q)
    q = [x + resid for x in q]
    q = [x * scale for x in q]
    for idx, f in enumerate(atoms):
        t = types(f[1], f[5])
        lines[a0 + idx] = f"{int(f[0]):7d} {f[1]:<8s} {float(f[2]):9.4f} {float(f[3]):9.4f} {float(f[4]):9.4f} {t:<4s} {f[6]:>3s} {f[7]:<4s} {q[idx]:10.6f}"
    open(outp, "w").write("\n".join(lines))
    print(outp, "net", round(sum(q), 6), {g: round(q[i[0]], 4) for g, i in groups.items()})

cls_tfsi = lambda n: re.sub(r"\d+", "", n)                       # N,S,O,C,F
typ_tfsi = lambda n, t: {"S": "sy", "O": "o", "C": "c3", "F": "f", "N": t}[re.sub(r"\d+", "", n)]
process("tfsi_raw.mol2", "tfsi.mol2", cls_tfsi, typ_tfsi, -1.0, SCALE)
cls_meoh = lambda n: "HC" if n in ("H2", "H3", "H4") else n      # three methyl H equivalent
process("meoh_raw.mol2", "meoh.mol2", cls_meoh, lambda n, t: t, 0.0, 1.0)
