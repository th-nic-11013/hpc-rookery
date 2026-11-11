"""Isolated-atom energies (E0s) for MLIP training from CP2K single points (G52).

Each run is three local texts: the .out (energy, convergence, CP2K version),
the .inp cp2k_make_input wrote with purpose="atom" (functional block, data
files, cutoffs, precision, multiplicity: PRINT_LEVEL LOW does not echo these
in the .out), and the subsys.inc (the one atom, its basis and potential, the
box). Text in, dicts out: no file I/O, no cluster.

An E0 only means something next to labels computed with the same settings,
so runs that disagree with each other, or with a given label run, are
refused. Per element the lowest-energy multiplicity is kept (the others are
reported), and when one multiplicity ran in two boxes the larger box is used
and the difference reported.

Energies: Ha -> eV with HA_EV, the constant cp2k_make_labels uses, so the
E0s and REF_energy share one conversion.
"""
import re

import numpy as np

from hyogo.cp2k import labels as cp2k_labels, output as cp2k_output
from hyogo.mace.dataset import Z_OF

HA_EV = cp2k_labels.HA_EV
BOX_TOL_EV = 1e-3
# Hund's-rule ground-state multiplicities of free atoms; elements outside the
# table get no ground-state check (warned)
GROUND = {"H": 2, "He": 1, "Li": 2, "Be": 1, "B": 2, "C": 3, "N": 4, "O": 3, "F": 2,
          "Ne": 1, "Na": 2, "Mg": 1, "Al": 2, "Si": 3, "P": 4, "S": 3, "Cl": 2, "Ar": 1,
          "Zr": 3}
ATOM_TAG = "(isolated atom, E0 reference)"
# settings that must agree between atoms and with the labels
COMPARED = ("xc", "basis_files", "potential_file", "cutoff", "rel_cutoff", "eps_default",
            "eps_pgf_orb")


def input_settings(text: str) -> dict:
    """The settings an E0 depends on, from a cp2k_make_input .inp: @SET
    values expanded; the &XC block without D3 (an atom has no dispersion)
    and without the HFX radius and memory (they follow the box, not the
    method)."""
    sets = {}
    for line in text.splitlines():
        m = re.match(r"@SET\s+(\w+)\s+(\S+)", line.strip())
        if m:
            sets[m.group(1)] = m.group(2)
    if any(line.strip().startswith("@IF") for line in text.splitlines()):
        raise ValueError("the input has @IF blocks (a restartable run type); an E0 input is a "
                         "single point from cp2k_make_input(purpose=\"atom\")")
    body = re.sub(r"\$\{(\w+)\}", lambda m: sets.get(m.group(1), m.group(0)), text)
    lines = [ln.split("!", 1)[0].strip() for ln in body.splitlines()
             if not ln.lstrip().startswith(("#", "@"))]
    lines = [" ".join(ln.split()) for ln in lines if ln]
    r = {"basis_files": [], "potential_file": None, "cutoff": None, "rel_cutoff": None,
         "eps_default": None, "eps_pgf_orb": None, "multiplicity": None, "charge": None,
         "uks": None, "smear": False, "stress": False, "xc": None,
         "atom": ATOM_TAG in text.split("\n", 1)[0],
         "functional": None}
    m = re.match(r"# CP2K \w+(?: \([^)]*\))?, (\w+)", text)
    r["functional"] = m.group(1) if m else None
    xc, in_xc, in_vdw = [], False, False
    for ln in lines:
        key, _, val = ln.partition(" ")
        up = key.upper()
        if up == "&XC":
            in_xc = True
            continue
        if in_xc:
            if ln.upper() == "&END XC":
                in_xc = False
            elif up == "&VDW_POTENTIAL":
                in_vdw = True
            elif ln.upper().startswith("&END VDW_POTENTIAL"):
                in_vdw = False
            elif not in_vdw and up not in ("CUTOFF_RADIUS", "MAX_MEMORY"):
                xc.append(ln.upper())
            continue
        if up == "BASIS_SET_FILE_NAME":
            r["basis_files"].append(val)
        elif up == "POTENTIAL_FILE_NAME":
            r["potential_file"] = val
        elif up == "CUTOFF" and r["cutoff"] is None:
            r["cutoff"] = float(val)
        elif up == "REL_CUTOFF":
            r["rel_cutoff"] = float(val)
        elif up == "EPS_DEFAULT":
            r["eps_default"] = float(val)
        elif up == "EPS_PGF_ORB":
            r["eps_pgf_orb"] = float(val)
        elif up == "MULTIPLICITY":
            r["multiplicity"] = int(val)
        elif up == "CHARGE":
            r["charge"] = int(val)
        elif up == "UKS":
            r["uks"] = val.upper() in (".TRUE.", "T", "TRUE")
        elif up == "&SMEAR":
            r["smear"] = val.upper() in ("", "ON", "T", ".TRUE.")
        elif up == "STRESS_TENSOR":
            r["stress"] = True
    r["xc"] = "\n".join(xc) or None
    return r


def subsys_settings(text: str) -> dict:
    """Symbols, kinds (basis, potential), periodicity and the box's widths
    (A) from a subsys.inc cp2k_make_subsys wrote."""
    s = cp2k_labels.read_subsys(text)
    kinds, cur = {}, None
    for raw in text.splitlines():
        t = raw.split("#", 1)[0].split()
        if not t:
            continue
        if t[0].upper() == "&KIND":
            cur = t[1]
            kinds[cur] = {"basis": None, "potential": None}
        elif t[0].upper().startswith("&END") and cur:
            cur = None
        elif cur and t[0].upper() == "BASIS_SET" and len(t) >= 2:
            kinds[cur]["basis"] = t[-1]
        elif cur and t[0].upper() == "POTENTIAL" and len(t) >= 2:
            kinds[cur]["potential"] = t[-1]
    cell = np.array(s["cell"], float)
    vol = abs(np.linalg.det(cell))
    widths = [float(vol / np.linalg.norm(np.cross(cell[(i + 1) % 3], cell[(i + 2) % 3])))
              for i in range(3)]
    return {"symbols": s["symbols"], "kinds": kinds,
            "periodic": "NONE" if s["pbc"] == ["F", "F", "F"] else "XYZ",
            "box_A": round(min(widths), 2)}


def _differs(a: dict, b: dict) -> list[str]:
    return [k for k in COMPARED if a.get(k) != b.get(k)]


def make_e0s(runs: list[dict], label: dict | None = None,
             allow_untested_version: bool = False) -> tuple[dict, dict]:
    """({Z: E0 in eV}, info) from isolated-atom runs.

    runs: [{"out_text", "inp_text", "subsys_text", "source", "sha256"}].
    label: {"inp_text", "subsys_text", "out_text"?} of one label run, to
    check the atoms used its settings (and CP2K version, with its .out)."""
    if not runs:
        raise ValueError("runs is empty: pass every isolated-atom run (.out, .inp, subsys.inc)")
    warnings, rows, ref, ref_src = [], [], None, None
    for run in runs:
        src = run["source"]
        out = cp2k_output.parse(run["out_text"])
        if not out["finished"]:
            raise ValueError(f"{src}: no PROGRAM ENDED (still running or killed); "
                             "get_job_status, then download the finished .out")
        if out["scf"]["not_converged"] or not out["scf"]["evaluations"] or out["energy_ha"] is None:
            raise ValueError(f"{src}: the SCF did not converge (or printed no energy); an E0 "
                             "from it is not a minimum. Rerun it (see the notes for Zr: "
                             "diagonalization with ADDED_MOS, no smearing)")
        if out["natoms"] != 1:
            raise ValueError(f"{src}: {out['natoms']} atoms; an E0 run is one isolated atom")
        version = cp2k_labels._version(out["version"])
        if version not in cp2k_labels.TESTED_VERSIONS:
            if not allow_untested_version:
                raise ValueError(f"{src}: CP2K {version} is not a version the tests cover "
                                 f"({', '.join(cp2k_labels.TESTED_VERSIONS)}); pass "
                                 "allow_untested_version=true after checking the .out")
            warnings.append(f"{src}: CP2K {version} is untested")
        inp = input_settings(run["inp_text"])
        if not inp["atom"]:
            raise ValueError(f"{src}: its .inp was not written by cp2k_make_input(purpose=\"atom\") "
                             "(no isolated-atom header); write the E0 inputs with that")
        if inp["smear"]:
            raise ValueError(f"{src}: smeared occupations give a fractional, higher-energy atom; "
                             "E0s use integer occupations (OT, or diagonalization without &SMEAR)")
        if inp["stress"]:
            raise ValueError(f"{src}: the input computes stress; an E0 input has none")
        sub = subsys_settings(run["subsys_text"])
        if len(sub["symbols"]) != 1:
            raise ValueError(f"{src}: subsys.inc has {len(sub['symbols'])} atoms; pass the "
                             "subsys.inc this run included")
        if sub["periodic"] != "NONE":
            raise ValueError(f"{src}: the box is periodic; an E0 needs PERIODIC NONE "
                             "(cp2k_make_subsys periodic=\"NONE\")")
        el = sub["symbols"][0]
        if el not in Z_OF:
            raise ValueError(f"{src}: unknown element {el!r}")
        m = inp["multiplicity"]
        if m is None or m < 1:
            raise ValueError(f"{src}: no MULTIPLICITY in the .inp")
        inp["version"] = version
        if ref is None:
            ref, ref_src = inp, src
        else:
            bad = _differs(ref, inp) + (["version"] if ref["version"] != version else [])
            if bad:
                raise ValueError(f"{src} and {ref_src} differ in {bad}: every E0 must use the "
                                 "same settings (and the labels' settings)")
        rows.append({"element": el, "multiplicity": m, "box_A": sub["box_A"],
                     "energy_ha": out["energy_ha"], "energy_ev": out["energy_ha"] * HA_EV,
                     "kind": sub["kinds"].get(el), "source": src, "sha256": run["sha256"],
                     "scf_steps": out["scf"]["steps"], "cp2k_warnings": out["warnings"]})
        for w in out["warnings"]:
            warnings.append(f"{src}: {w}")

    # one run per (element, multiplicity, box); one kind per element
    seen = {}
    for r in rows:
        key = (r["element"], r["multiplicity"], r["box_A"])
        if key in seen:
            raise ValueError(f"{r['source']} and {seen[key]} are both {r['element']} multiplicity "
                             f"{r['multiplicity']} in a {r['box_A']} A box; pass one")
        seen[key] = r["source"]
    kinds = {}
    for r in rows:
        k = kinds.setdefault(r["element"], (r["kind"], r["source"]))
        if k[0] != r["kind"]:
            raise ValueError(f"{r['source']} uses {r['kind']} for {r['element']}, {k[1]} uses "
                             f"{k[0]}: one basis and potential per element")

    if label is not None:
        lab = input_settings(label["inp_text"])
        if lab["atom"]:
            raise ValueError("label is an isolated-atom input; pass one of the label runs")
        bad = _differs(lab, ref)
        if bad:
            raise ValueError(f"the atoms and the labels differ in {bad}: the E0s would sit on "
                             "another energy zero. Rewrite the atom inputs with the labels' "
                             "functional, kinds, cutoff, rel_cutoff and precision")
        if label.get("out_text"):
            lv = cp2k_labels._version(cp2k_output.parse(label["out_text"])["version"])
            if lv != ref["version"]:
                raise ValueError(f"the labels ran on CP2K {lv}, the atoms on {ref['version']}")
        lkinds = subsys_settings(label["subsys_text"])["kinds"]
        for el, (k, src) in kinds.items():
            if el not in lkinds:
                warnings.append(f"{el} is not in the labels' subsys.inc")
            elif lkinds[el] != k:
                raise ValueError(f"{src}: {el} uses {k}, the labels use {lkinds[el]}")
        lack = sorted(set(lkinds) - set(kinds))
        if lack:
            warnings.append(f"no E0 run for {lack}, which the labels contain")

    e0s, per = {}, {}
    for el in sorted({r["element"] for r in rows}, key=lambda e: Z_OF[e]):
        mine = [r for r in rows if r["element"] == el]
        by_m = {}
        for r in mine:
            by_m.setdefault(r["multiplicity"], []).append(r)
        best_of = {m: max(rs, key=lambda r: r["box_A"]) for m, rs in by_m.items()}
        m_best = min(best_of, key=lambda m: best_of[m]["energy_ev"])
        chosen = best_of[m_best]
        others = [{"multiplicity": m, "box_A": r["box_A"],
                   "delta_ev": round(r["energy_ev"] - chosen["energy_ev"], 6)}
                  for m, r in sorted(best_of.items()) if m != m_best]
        boxes = sorted(by_m[m_best], key=lambda r: r["box_A"])
        box_delta = (round(boxes[-1]["energy_ev"] - boxes[0]["energy_ev"], 6)
                     if len(boxes) > 1 else None)
        if el not in GROUND:
            warnings.append(f"{el}: no ground-state multiplicity on record; {m_best} kept as the "
                            "lowest of those run")
        elif m_best != GROUND[el]:
            warnings.append(f"{el}: the lowest run is multiplicity {m_best}, but the free atom's "
                            f"ground state is {GROUND[el]}: check the SCF reached the right state")
        if el in GROUND and GROUND[el] not in by_m:
            warnings.append(f"{el}: the ground-state multiplicity {GROUND[el]} was not run")
        if len(by_m) == 1 and GROUND.get(el, 1) > 1 and el != "H":
            warnings.append(f"{el}: only multiplicity {m_best} was run; run another to confirm "
                            "it is the lowest")
        if box_delta is not None and abs(box_delta) > BOX_TOL_EV:
            warnings.append(f"{el}: the {boxes[0]['box_A']} A and {boxes[-1]['box_A']} A boxes "
                            f"differ by {box_delta * 1000:.1f} meV (> {BOX_TOL_EV * 1000:.0f}): "
                            "use a larger box, or tighten the grid for atoms and labels alike")
        e0s[Z_OF[el]] = chosen["energy_ev"]
        per[el] = {"Z": Z_OF[el], "multiplicity": m_best, "box_A": chosen["box_A"],
                   "energy_ha": chosen["energy_ha"], "e0_ev": chosen["energy_ev"],
                   "kind": chosen["kind"], "source": chosen["source"],
                   "sha256": chosen["sha256"], "others": others,
                   "box_check_ev": box_delta}
    info = {"e0s": {str(z): v for z, v in e0s.items()}, "per_element": per,
            "settings": {**{k: ref[k] for k in COMPARED if k != "xc"},
                         "functional": ref["functional"], "cp2k_version": ref["version"]},
            "label_checked": label is not None, "runs": len(rows), "warnings": warnings}
    return e0s, info
