#!/usr/bin/env python3
"""Pick a frame from an extxyz label file, make molecules whole, identify Li / TFSI / MeOH,
canonically order their atoms, replicate NxNxN, and write molecule templates + replicated PDB.
Canonical orders: TFSI = N, S1,O,O,C,F,F,F, S2,O,O,C,F,F,F (15)   MeOH = C,O,HO,HC,HC,HC (6).
usage: make_molecules.py LABELS.extxyz --frame median|<index> --replicate 2 --energy-key REF_energy"""
import argparse
import numpy as np
from ase.io import read
from ase.neighborlist import natural_cutoffs, neighbor_list

ap = argparse.ArgumentParser()
ap.add_argument("labels")
ap.add_argument("--frame", default="median")
ap.add_argument("--replicate", type=int, default=2)
ap.add_argument("--energy-key", default="REF_energy")
args = ap.parse_args()

frames = read(args.labels, ":")
if args.frame == "median":
    E = np.array([f.info[args.energy_key] / len(f) for f in frames])
    k = int(np.argsort(E)[len(E) // 2])
else:
    k = int(args.frame)
a = frames[k]
print("frame", k, a.info.get("structure_name", ""), "natoms", len(a))
L = a.cell.lengths()[0]
assert np.allclose(a.cell.angles(), 90, atol=1e-3) and np.allclose(a.cell.lengths(), L, atol=1e-3), "cubic cell required"
sym = np.array(a.get_chemical_symbols())

# bonds (minimum image), molecules as connected components
i, j = neighbor_list("ij", a, natural_cutoffs(a, mult=1.15))
adj = {x: set() for x in range(len(a))}
for x, y in zip(i, j):
    if "Li" in (sym[x], sym[y]):      # Li+ is a free ion; Li-O contacts are not covalent bonds
        continue
    adj[x].add(y)
seen, comps = set(), []
for s in range(len(a)):
    if s in seen:
        continue
    st, comp = [s], []
    seen.add(s)
    while st:
        u = st.pop(); comp.append(u)
        for v in adj[u]:
            if v not in seen:
                seen.add(v); st.append(v)
    comps.append(sorted(comp))

def unwrap(comp):
    """positions of a molecule made whole by BFS with minimum-image bond vectors"""
    pos = {comp[0]: a.positions[comp[0]].copy()}
    st = [comp[0]]
    while st:
        u = st.pop()
        for v in adj[u]:
            if v not in pos:
                d = a.positions[v] - a.positions[u]
                d -= L * np.round(d / L)
                pos[v] = pos[u] + d; st.append(v)
    return pos

mols = {"Li": [], "TFSI": [], "MEOH": []}
for comp in comps:
    s = sym[comp]
    if len(comp) == 1 and s[0] == "Li":
        mols["Li"].append([("Li", a.positions[comp[0]])])
    elif "S" in s and len(comp) == 15:
        pos = unwrap(comp)
        N = [c for c in comp if sym[c] == "N"][0]
        Ss = sorted([c for c in adj[N] if sym[c] == "S"])
        assert len(Ss) == 2
        order = [N]
        for S in Ss:
            Os = sorted(c for c in adj[S] if sym[c] == "O")
            C = [c for c in adj[S] if sym[c] == "C"][0]
            Fs = sorted(c for c in adj[C] if sym[c] == "F")
            assert len(Os) == 2 and len(Fs) == 3
            order += [S] + Os + [C] + Fs
        mols["TFSI"].append([(sym[c], pos[c]) for c in order])
    elif "C" in s and "S" not in s and len(comp) == 6:
        pos = unwrap(comp)
        C = [c for c in comp if sym[c] == "C"][0]
        O = [c for c in adj[C] if sym[c] == "O"][0]
        HO = [c for c in adj[O] if sym[c] == "H"][0]
        HC = sorted(c for c in adj[C] if sym[c] == "H")
        mols["MEOH"].append([(sym[c], pos[c]) for c in [C, O, HO] + HC])
    else:
        raise SystemExit(f"unrecognised fragment {list(s)}")
n = {k_: len(v) for k_, v in mols.items()}
print(n)
assert n["Li"] == n["TFSI"], "Li and TFSI counts differ: not a neutral LiTFSI/MeOH box"

def com(m):
    return np.mean([p for _, p in m], axis=0)

R = args.replicate
rep = []
for name in ["TFSI", "MEOH", "Li"]:               # residue order in the system
    for m in mols[name]:
        sh = -L * np.floor(com(m) / L)            # wrap molecule COM into the original box
        m = [(e, p + sh) for e, p in m]
        for ix in range(R):
            for iy in range(R):
                for iz in range(R):
                    rep.append((name, [(e, p + L * np.array([ix, iy, iz])) for e, p in m]))
print("residues", len(rep), "box", R * L)

def pdb_lines(residues, boxlen=None):
    out = []
    if boxlen:
        out.append(f"CRYST1{boxlen:9.3f}{boxlen:9.3f}{boxlen:9.3f}  90.00  90.00  90.00 P 1           1")
    n = 0
    for r, (name, m) in enumerate(residues, 1):
        cnt = {}
        for e, p in m:
            cnt[e] = cnt.get(e, 0) + 1
            n += 1
            an = f"{e}{cnt[e]}"
            out.append(f"ATOM  {n:5d} {an:<4s} {name[:3] if name != 'MEOH' else 'MOH':>3s} A{r:4d}    {p[0]:8.3f}{p[1]:8.3f}{p[2]:8.3f}  1.00  0.00          {e:>2s}")
        out.append("TER")
    out.append("END")
    return "\n".join(out) + "\n"

open("tfsi_template.pdb", "w").write(pdb_lines([("TFSI", mols["TFSI"][0])]))
open("meoh_template.pdb", "w").write(pdb_lines([("MEOH", mols["MEOH"][0])]))
open("system_replicated.pdb", "w").write(pdb_lines(rep, R * L))
print("wrote templates + system_replicated.pdb;", sum(len(m) for _, m in rep), "atoms")
