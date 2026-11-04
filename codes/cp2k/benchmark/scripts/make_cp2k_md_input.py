"""Write a CP2K NVE AIMD benchmark input (and a short smoke version) from an extended xyz.

Usage (text work only, fine on the login node):
    {{CP2K_PYTHON}} -I make_cp2k_md_input.py STRUCT.xyz --name NAME [options]
Writes <out-dir>/NAME.inp (--steps MD steps, default 10) and <out-dir>/NAME-smoke.inp
(--smoke-steps, default 3).

The xyz must be extended xyz with Lattice="ax ay az bx by bz cx cy cz" on line 2 (rows
are the cell vectors), as written by ASE. Each element gets BASIS (default
DZVP-MOLOPT-SR-GTH) and the GTH-PBE pseudopotential with the same valence count q,
both looked up in the CP2K data folder, so the q never has to be guessed (Zr q12,
Ti q12, Cu q11, N q5 ...). GTH-PBE potentials are the usual choice for revPBE too.

Defaults: revPBE + D3 (zero damping), 450/60 Ry, Gamma point, NVE 0.5 fs from 300 K,
OT/DIIS with FULL_SINGLE_INVERSE, EPS_SCF 1e-6, IGNORE_CONVERGENCE_FAILURE (so a slow
SCF shows up as time, not as a crashed benchmark).
--stretch-ch L moves every H bonded to C (< 1.2 A) along the bond to L (use 1.08 for
X-ray structures, whose C-H bonds come out about 0.95 A).

This is the by-hand fallback. The agent's path is the cp2k_bench_input tool,
which writes the same inputs (conformance checks it, apart from the "Written
by" line) with the kinds read as cp2k_kinds does; there, C-H fixes are
yagura's structure_stretch_xh, run first, and --stretch-ch is kept here only
for by-hand use.
"""
import argparse
import re
import sys
from pathlib import Path

import numpy as np

TEMPLATE = """\
# {name}: {natoms} atoms, {formula}, {nelec} valence electrons.
# Source: {src}{chnote}
# Written by make_cp2k_md_input.py ({cmd}).
&GLOBAL
  PRINT_LEVEL LOW
  PROJECT {project}
  RUN_TYPE MD
&END GLOBAL

&MOTION
  &MD
    ENSEMBLE NVE
    STEPS {steps}
    TEMPERATURE 300.0
    TIMESTEP 0.5
  &END MD
&END MOTION

&FORCE_EVAL
  METHOD QS
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME GTH_POTENTIALS
    CHARGE {charge}
    &MGRID
      CUTOFF {cutoff}
      REL_CUTOFF {rel}
    &END MGRID
    &QS
      EPS_DEFAULT 1.0E-12
      EXTRAPOLATION ASPC
      EXTRAPOLATION_ORDER 3
    &END QS
    &SCF
      IGNORE_CONVERGENCE_FAILURE
      SCF_GUESS ATOMIC
      EPS_SCF 1.0E-6
      MAX_SCF 30
      &OT ON
        MINIMIZER DIIS
        PRECONDITIONER FULL_SINGLE_INVERSE
      &END OT
      &OUTER_SCF
        EPS_SCF 1.0E-6
        MAX_SCF 10
      &END OUTER_SCF
      &PRINT
        &RESTART OFF
        &END RESTART
      &END PRINT
    &END SCF
    &XC
      &XC_FUNCTIONAL
        &PBE
          PARAMETRIZATION {param}
        &END PBE
      &END XC_FUNCTIONAL
{vdw}    &END XC
  &END DFT
  &SUBSYS
    &CELL
{cell}
      PERIODIC XYZ
    &END CELL
    &COORD
{coord}
    &END COORD
{kinds}
  &END SUBSYS
&END FORCE_EVAL
"""

VDW = """\
      &VDW_POTENTIAL
        POTENTIAL_TYPE PAIR_POTENTIAL
        &PAIR_POTENTIAL
          TYPE {type}
          PARAMETER_FILE_NAME dftd3.dat
          REFERENCE_FUNCTIONAL {ref}
        &END PAIR_POTENTIAL
      &END VDW_POTENTIAL
"""


def read_extxyz(path):
    lines = Path(path).read_text().splitlines()
    n = int(lines[0])
    m = re.search(r'Lattice="([^"]+)"', lines[1])
    if not m:
        sys.exit(f"{path}: line 2 has no Lattice=\"...\" (need extended xyz with the cell)")
    cell = np.array(m.group(1).split(), float).reshape(3, 3)
    el = [l.split()[0] for l in lines[2:2 + n]]
    xyz = np.array([[float(v) for v in l.split()[1:4]] for l in lines[2:2 + n]])
    return cell, el, xyz


def valence(data, element, basis):
    """q of BASIS for ELEMENT from BASIS_MOLOPT, checked against GTH_POTENTIALS."""
    q = None
    for line in (Path(data) / "BASIS_MOLOPT").read_text().splitlines():
        t = line.split()
        if len(t) >= 2 and t[0] == element and basis in t[1:]:
            for tok in t[1:]:
                mm = re.fullmatch(re.escape(basis) + r"-q(\d+)", tok)
                if mm:
                    q = int(mm.group(1))
            if q is None:
                sys.exit(f"{element} {basis}: found, but no -qN alias to read the valence from")
            break
    if q is None:
        sys.exit(f"no {basis} for {element} in {data}/BASIS_MOLOPT")
    pot = f"GTH-PBE-q{q}"
    if not any(l.split()[:2] == [element, pot] for l in (Path(data) / "GTH_POTENTIALS").read_text().splitlines()):
        sys.exit(f"no {element} {pot} in {data}/GTH_POTENTIALS")
    return q, pot


def stretch_ch(cell, el, xyz, length):
    inv = np.linalg.inv(cell)
    el = np.array(el)
    carbons = xyz[el == "C"]
    moved = 0
    if len(carbons) == 0:
        return xyz, 0
    for h in np.where(el == "H")[0]:
        d = (carbons - xyz[h]) @ inv
        d -= np.round(d)
        vec = d @ cell
        r = np.linalg.norm(vec, axis=1)
        k = r.argmin()
        if r[k] < 1.2:
            xyz[h] = xyz[h] - vec[k] / r[k] * (length - r[k])
            moved += 1
    return xyz, moved


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("xyz")
    ap.add_argument("--name", required=True, help="project and file name, e.g. UiO66-prim")
    ap.add_argument("--out-dir", default="inputs")
    ap.add_argument("--steps", type=int, default=10)
    ap.add_argument("--smoke-steps", type=int, default=3)
    ap.add_argument("--xc", choices=["revPBE", "PBE"], default="revPBE")
    ap.add_argument("--d3", choices=["zero", "bj", "none"], default="zero")
    ap.add_argument("--cutoff", type=int, default=450)
    ap.add_argument("--rel-cutoff", type=int, default=60)
    ap.add_argument("--basis", default="DZVP-MOLOPT-SR-GTH")
    ap.add_argument("--charge", type=int, default=0)
    ap.add_argument("--data-dir", required=True,
                    help="the build's data folder: its software_registry data_dir")
    ap.add_argument("--stretch-ch", type=float, metavar="L")
    a = ap.parse_args()

    cell, el, xyz = read_extxyz(a.xyz)
    elements = list(dict.fromkeys(el))
    q = {e: valence(a.data_dir, e, a.basis) for e in elements}
    nelec = sum(q[e][0] for e in el) - a.charge
    if nelec % 2:
        print(f"WARNING: {nelec} electrons (odd): needs UKS and MULTIPLICITY, add them by hand")
    chnote = ""
    if a.stretch_ch:
        xyz, moved = stretch_ch(cell, el, xyz, a.stretch_ch)
        chnote = f"; {moved} C-H bonds set to {a.stretch_ch:.2f} A"
        print(f"moved {moved} H bonded to C to C-H = {a.stretch_ch} A")

    vol = abs(np.linalg.det(cell))
    widths = [vol / np.linalg.norm(np.cross(cell[(i + 1) % 3], cell[(i + 2) % 3])) for i in range(3)]
    print("cell perpendicular widths [A]:", " ".join(f"{w:.2f}" for w in widths))
    if min(widths) < 10:
        print("WARNING: a cell width is below 10 A; Gamma-point sampling may be poor, consider a supercell")

    formula = "".join(f"{e}{el.count(e)}" for e in elements)
    vdw = "" if a.d3 == "none" else VDW.format(type="DFTD3" if a.d3 == "zero" else "DFTD3(BJ)", ref=a.xc)
    common = dict(
        name=a.name, natoms=len(el), formula=formula, nelec=nelec, src=Path(a.xyz).name, chnote=chnote,
        cmd=" ".join(Path(sys.argv[0]).name if i == 0 else s for i, s in enumerate(sys.argv)),
        charge=a.charge, cutoff=a.cutoff, rel=a.rel_cutoff, param=a.xc.upper() if a.xc == "revPBE" else "ORIG",
        vdw=vdw,
        cell="\n".join(f"      {n} {v[0]:14.8f} {v[1]:14.8f} {v[2]:14.8f}" for n, v in zip("ABC", cell)),
        coord="\n".join(f"      {e:2s} {p[0]:14.8f} {p[1]:14.8f} {p[2]:14.8f}" for e, p in zip(el, xyz)),
        kinds="\n".join(f"    &KIND {e}\n      BASIS_SET {a.basis}\n      POTENTIAL {q[e][1]}\n    &END KIND"
                        for e in elements),
    )
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, steps in [(a.name, a.steps), (f"{a.name}-smoke", a.smoke_steps)]:
        (out / f"{name}.inp").write_text(TEMPLATE.format(project=name, steps=steps, **common))
        print(f"wrote {out / name}.inp ({steps} MD steps)")
    print(f"{len(el)} atoms {formula}, {nelec} valence electrons, kinds: "
          + ", ".join(f"{e} {q[e][1]}" for e in elements))


if __name__ == "__main__":
    main()
