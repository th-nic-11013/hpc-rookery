"""The text of a CP2K NVE AIMD benchmark input and its short smoke version.

The input-writing part of codes/cp2k/benchmark/scripts/make_cp2k_md_input.py:
the per-element valence and potential come in as `kinds` (cp2k_kinds, read
from the build's own data folder on the login node), and X-H fixes happen
before (yagura's structure_stretch_xh), so this only turns a checked
structure into text. The output is the script's, line for line, apart from
the "Written by" comment; conformance checks that. The template is a copy,
not an import, because the script runs on its own on a login node.
"""
import re

import numpy as np

TEMPLATE = """\
# {name}: {natoms} atoms, {formula}, {nelec} valence electrons.
# Source: {src}{chnote}
# Written by {cmd}.
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


def read_extxyz(text: str):
    # deliberately a copy, not an import: rookery meets yagura and hyogo through tools
    lines = text.splitlines()
    n = int(lines[0])
    m = re.search(r'Lattice="([^"]+)"', lines[1])
    if not m:
        raise ValueError("line 2 has no Lattice=\"...\" (need extended xyz with the cell)")
    cell = np.array(m.group(1).split(), float).reshape(3, 3)
    el = [l.split()[0] for l in lines[2:2 + n]]
    xyz = np.array([[float(v) for v in l.split()[1:4]] for l in lines[2:2 + n]])
    return cell, el, xyz


def check_args(name: str, xc: str, d3: str) -> None:
    if xc not in ("revPBE", "PBE"):
        raise ValueError('xc is "revPBE" or "PBE"')
    if d3 not in ("zero", "bj", "none"):
        raise ValueError('d3 is "zero", "bj" or "none"')
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError(f"name is a project and file name such as UiO66-prim, not {name!r}")


def bench_inputs(text: str, src: str, name: str, kinds: dict, steps: int = 10,
                 smoke_steps: int = 3, xc: str = "revPBE", d3: str = "zero",
                 cutoff: int = 450, rel_cutoff: int = 60, charge: int = 0) -> tuple[dict, dict]:
    """{file name: text} for NAME.inp and NAME-smoke.inp, and a summary.

    kinds is cp2k_kinds' `kinds` mapping {element: {q, basis, potential}}.
    """
    check_args(name, xc, d3)
    cell, el, xyz = read_extxyz(text)
    elems = list(dict.fromkeys(el))
    missing = [e for e in elems if e not in kinds]
    if missing:
        raise ValueError(f"no kind for {missing}")
    nelec = sum(kinds[e]["q"] for e in el) - charge
    vol = abs(np.linalg.det(cell))
    widths = [vol / np.linalg.norm(np.cross(cell[(i + 1) % 3], cell[(i + 2) % 3])) for i in range(3)]
    warnings = []
    if nelec % 2:
        warnings.append(f"{nelec} electrons (odd): needs UKS and MULTIPLICITY, add them by hand")
    if min(widths) < 10:
        warnings.append("a cell width is below 10 A; Gamma-point sampling may be poor, consider a supercell")

    formula = "".join(f"{e}{el.count(e)}" for e in elems)
    vdw = "" if d3 == "none" else VDW.format(type="DFTD3" if d3 == "zero" else "DFTD3(BJ)", ref=xc)
    common = dict(
        name=name, natoms=len(el), formula=formula, nelec=nelec, src=src, chnote="",
        cmd="cp2k_bench_input", charge=charge, cutoff=cutoff, rel=rel_cutoff,
        param=xc.upper() if xc == "revPBE" else "ORIG", vdw=vdw,
        cell="\n".join(f"      {n} {v[0]:14.8f} {v[1]:14.8f} {v[2]:14.8f}" for n, v in zip("ABC", cell)),
        coord="\n".join(f"      {e:2s} {p[0]:14.8f} {p[1]:14.8f} {p[2]:14.8f}" for e, p in zip(el, xyz)),
        kinds="\n".join(f"    &KIND {e}\n      BASIS_SET {kinds[e]['basis']}\n"
                        f"      POTENTIAL {kinds[e]['potential']}\n    &END KIND" for e in elems),
    )
    texts = {f"{project}.inp": TEMPLATE.format(project=project, steps=n, **common)
             for project, n in ((name, steps), (f"{name}-smoke", smoke_steps))}
    info = {"formula": formula, "natoms": len(el), "electrons": nelec,
            "widths": [round(float(w), 2) for w in widths],
            "kinds": {e: kinds[e]["potential"] for e in elems}, "warnings": warnings}
    return texts, info
