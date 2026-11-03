"""Write FULL_ALL single-point (ENERGY_FORCE) benchmark inputs from the MD inputs.

Usage (text work, fine on the front end):
    {{CP2K_PYTHON}} -I make_sp_inputs.py
For each MD input below it writes inputs/<name>-sp.inp with:
  - RUN_TYPE ENERGY_FORCE and no &MOTION block;
  - the SCF adopted for big cells (2026-10-18 SCF tests): OT DIIS + FULL_ALL with
    ENERGY_GAP 0.05, atomic guess, EPS_SCF 1e-6, MAX_SCF 50, OUTER_SCF 10, and
    NO IGNORE_CONVERGENCE_FAILURE, so an unconverged SCF stops the run;
  - &FORCE_EVAL &PRINT &FORCES ON, so the forces can be compared across layouts.
Structure, revPBE-D3, DZVP-MOLOPT-SR-GTH, GTH-PBE and 450/60 Ry are unchanged.

Inputs (all with the UiO-66 DFT settings):
  UiO66-prim.inp          114 atoms  -> UiO66-prim-sp.inp   (stage 1, smoke)
  UiO66-conv-fullall.inp  456 atoms  -> UiO66-conv-sp.inp   (stage 2, scan)
  H2O-128-revpbe.inp      384 atoms  -> H2O-128-sp.inp      (stage 3, validate)
  H2O-64-revpbe.inp       192 atoms  -> H2O-64-sp.inp       (spare, same size as the 114-atom cell)
"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
INP = HERE / "inputs"
SOURCES = {
    "UiO66-prim.inp": "UiO66-prim-sp",
    "UiO66-conv-fullall.inp": "UiO66-conv-sp",
    "H2O-128-revpbe.inp": "H2O-128-sp",
    "H2O-64-revpbe.inp": "H2O-64-sp",
}

SCF = """    &SCF
      SCF_GUESS ATOMIC
      EPS_SCF 1.0E-6
      MAX_SCF 50
      &OT ON
        MINIMIZER DIIS
        PRECONDITIONER FULL_ALL
        ENERGY_GAP 0.05
      &END OT
      &OUTER_SCF
        EPS_SCF 1.0E-6
        MAX_SCF 10
      &END OUTER_SCF
      &PRINT
        &RESTART OFF
        &END RESTART
      &END PRINT
    &END SCF"""

FORCES = """  &PRINT
    &FORCES ON
    &END FORCES
  &END PRINT"""


def convert(text, project):
    t = re.sub(r"(?m)^(\s*PROJECT)\s+\S+", rf"\1 {project}", text, count=1)
    t, n = re.subn(r"(?m)^(\s*RUN_TYPE)\s+MD\b", r"\1 ENERGY_FORCE", t)
    assert n == 1, "RUN_TYPE MD not found"
    t, n = re.subn(r"(?ms)^&MOTION\b.*?^&END MOTION\s*\n", "", t)
    assert n == 1, "&MOTION block not found"
    t, n = re.subn(r"(?ms)^\s*&SCF\b.*?^\s*&END SCF", SCF, t)
    assert n == 1, "&SCF block not found"
    t, n = re.subn(r"(?m)^(\s*METHOD QS\s*)$", r"\1\n" + FORCES, t)
    assert n == 1, "METHOD QS not found"
    for must in ("RUN_TYPE ENERGY_FORCE", "PRECONDITIONER FULL_ALL", "ENERGY_GAP 0.05",
                 "REVPBE", "DFTD3", "CUTOFF 450", "DZVP-MOLOPT-SR-GTH", "&FORCES ON"):
        assert must in t, must
    for gone in ("IGNORE_CONVERGENCE_FAILURE", "&MOTION", "FULL_SINGLE_INVERSE"):
        assert gone not in t, gone
    return t


def main():
    for src, name in SOURCES.items():
        out = INP / f"{name}.inp"
        out.write_text(convert((INP / src).read_text(), name))
        print(f"wrote {out} (from {src})")


if __name__ == "__main__":
    main()
