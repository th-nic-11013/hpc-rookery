"""MLIP training labels from CP2K single points (G43): each run's .out plus
the subsys.inc it ran on, written as one extended-XYZ file in the keys MACE
reads.

Text in, text out: no file I/O, no cluster. The .out is the record (CP2K
version, SCF convergence, warnings, D3 energy); the extxyz is a product
regenerated from it, so every frame carries its source path and sha256.

Units and signs:
- energy: Ha -> eV; forces: Ha/bohr -> eV/A;
- stress: CP2K prints a pressure-like tensor in GPa (negative = the cell
  wants to contract: HSE06's 1/3 trace is ~0 at its own relaxed cell, and
  the smaller-cell r2SCAN family is negative there, UiO-66 E1 2026-11-09).
  ASE and MACE use the opposite sign in eV/A^3, so REF_stress = -tensor /
  160.21766208, written in Voigt order (xx yy zz yz xz xy).

D3 is never subtracted: the .out prints the D3 energy but not its share of
the forces or stress, so a "D3-free" label made that way would mix two
potential-energy surfaces. Frames with and without D3 are refused together.
"""
import re

import numpy as np

from hyogo.cp2k import output as cp2k_output

HA_EV = 27.211386245988
BOHR_A = 0.529177210903
GPA_PER_EV_A3 = 160.21766208
# CP2K versions whose .out layout the tests cover (tests/fixtures/cp2k_labels)
TESTED_VERSIONS = ("2026.2",)
VOIGT = ((0, 0), (1, 1), (2, 2), (1, 2), (0, 2), (0, 1))


def read_subsys(text: str) -> dict:
    """Cell, periodicity, symbols and Cartesian positions (A) from a
    subsys.inc written by cp2k_make_subsys. Refuses anything else
    (scaled coordinates, ABC cells, units)."""
    cell, pbc, sym, pos, sect = {}, None, [], [], None
    for raw in text.splitlines():
        s = raw.split("#", 1)[0].split()
        if not s:
            continue
        key = s[0].upper()
        if key in ("&CELL", "&COORD", "&KIND"):
            sect = key      # &KIND lines (basis, potential) are skipped
            continue
        if key.startswith("&END"):
            sect = None
            continue
        if sect == "&CELL":
            if key in ("A", "B", "C") and len(s) == 4:
                cell[key] = [float(v) for v in s[1:]]
            elif key == "PERIODIC":
                pbc = s[1].upper()
            elif key == "SYMMETRY":
                pass
            else:
                raise ValueError(f"subsys.inc &CELL line {raw.strip()!r} is not what "
                                 "cp2k_make_subsys writes (A/B/C vectors in A)")
        elif sect == "&COORD":
            if key in ("SCALED", "UNIT"):
                raise ValueError("subsys.inc uses SCALED or UNIT coordinates; write it with "
                                 "cp2k_make_subsys (Cartesian A)")
            if len(s) != 4:
                raise ValueError(f"subsys.inc &COORD line {raw.strip()!r} is not 'El x y z'")
            sym.append(s[0])
            pos.append([float(v) for v in s[1:]])
    if set(cell) != {"A", "B", "C"} or not sym:
        raise ValueError("subsys.inc has no complete &CELL (A, B, C) or no &COORD: pass the "
                         "subsys.inc the run included")
    pbc = pbc or "XYZ"
    flags = ["T" if ax in pbc and pbc != "NONE" else "F" for ax in "XYZ"]
    return {"cell": [cell[k] for k in "ABC"], "pbc": flags, "symbols": sym,
            "positions": pos}


def _version(v: str | None) -> str | None:
    m = re.search(r"(\d{4}\.\d+)", v or "")
    return m.group(1) if m else None


def _q(v: str) -> str:
    return '"' + v.replace('"', "'") + '"'


def frame(out_text: str, subsys_text: str, source: str, sha256: str,
          require_stress: bool = True, allow_untested_version: bool = False) -> tuple[dict, list]:
    """One labelled frame (a dict of arrays and info) and its warnings."""
    r = cp2k_output.parse(out_text, vectors=True)
    s = read_subsys(subsys_text)
    w, n = [], len(s["symbols"])
    ver = _version(r["version"])
    if ver not in TESTED_VERSIONS:
        msg = (f"{source}: CP2K {ver or 'version unknown'} is not a tested layout "
               f"({', '.join(TESTED_VERSIONS)}); the .out text differs between versions")
        if not allow_untested_version:
            raise ValueError(msg + ". Add a fixture test for it, or pass "
                             "allow_untested_version=true and check the result")
        w.append(msg)
    if not r["finished"]:
        raise ValueError(f"{source}: no PROGRAM ENDED (still running or killed: get_job_status)")
    if r["run_type"] != "ENERGY_FORCE":
        raise ValueError(f"{source}: run type {r['run_type']}, not ENERGY_FORCE: labels come from "
                         "single points (cp2k_make_input purpose=\"label\")")
    if r["scf"]["evaluations"] < 1 or r["scf"]["not_converged"]:
        raise ValueError(f"{source}: SCF not converged; its energy and forces are not labels")
    if r["energy_ha"] is None or not r["forces"] or "vectors" not in r["forces"]:
        raise ValueError(f"{source}: no energy or no force printout (FORCES ON; "
                         "cp2k_make_input purpose=\"label\" sets it)")
    nf = len(r["forces"]["vectors"])
    if not (nf == n and (r["natoms"] in (None, n))):
        raise ValueError(f"{source}: {nf} force rows and {r['natoms']} atoms in the .out, but "
                         f"{n} atoms in its subsys.inc: pass the subsys.inc this run included")
    if r["stress_gpa"] is None:
        if require_stress:
            raise ValueError(f"{source}: no analytical stress tensor (cp2k_make_input "
                             "purpose=\"label\" turns it on), or pass require_stress=false")
        stress = None
    else:
        t = np.array(r["stress_gpa"]["tensor"], dtype=float)
        stress = -t / GPA_PER_EV_A3
    for x in r["warnings"]:
        if x.startswith("CP2K warning"):
            w.append(f"{source}: {x}")
    f = {"symbols": s["symbols"], "positions": np.array(s["positions"]),
         "cell": np.array(s["cell"]), "pbc": s["pbc"],
         "energy": r["energy_ha"] * HA_EV,
         "forces": np.array(r["forces"]["vectors"]) * HA_EV / BOHR_A,
         "stress": stress,
         "dispersion": None if r["dispersion_ha"] is None else r["dispersion_ha"] * HA_EV,
         "source": source, "sha256": sha256, "version": r["version"], "project": r["project"]}
    return f, w


def to_extxyz(frames: list[dict], config_types: list, head: str | None) -> str:
    out = []
    for f, ct in zip(frames, config_types):
        props = "species:S:1:pos:R:3:REF_forces:R:3"
        info = [f"Lattice={_q(' '.join(f'{v:.10f}' for v in f['cell'].ravel()))}",
                f"Properties={props}", f"REF_energy={f['energy']:.10f}"]
        if f["stress"] is not None:
            info.append("REF_stress=" + _q(" ".join(f"{f['stress'][i, j]:.12e}" for i, j in VOIGT)))
        if ct:
            info.append(f"config_type={_q(ct)}")
        if head:
            info.append(f"head={_q(head)}")
        if f["dispersion"] is not None:
            info.append(f"dispersion_eV={f['dispersion']:.10f}")
        info += [f"source={_q(f['source'])}", f"source_sha256={f['sha256']}",
                 f"cp2k_version={_q(f['version'] or '')}", f"project={_q(f['project'] or '')}",
                 f"pbc={_q(' '.join(f['pbc']))}"]
        out.append(str(len(f["symbols"])))
        out.append(" ".join(info))
        for el, p, fv in zip(f["symbols"], f["positions"], f["forces"]):
            out.append(f"{el:<3}" + "".join(f"{v:18.10f}" for v in p)
                       + "".join(f"{v:20.12e}" for v in fv))
    return "\n".join(out) + "\n"


def make_labels(items: list[dict], head: str | None = None, require_stress: bool = True,
                allow_untested_version: bool = False) -> tuple[str, dict]:
    """items: [{"out_text", "subsys_text", "source", "sha256", "config_type"}].
    Returns the extxyz text and a summary."""
    if not items:
        raise ValueError("frames is empty: pass [{out, subsys}, ...]")
    frames, warns, cts = [], [], []
    for it in items:
        f, w = frame(it["out_text"], it["subsys_text"], it["source"], it["sha256"],
                     require_stress, allow_untested_version)
        frames.append(f)
        warns += w
        cts.append(it.get("config_type"))
    has_d3 = {f["dispersion"] is not None for f in frames}
    if len(has_d3) > 1:
        mixed = [f["source"] for f in frames if f["dispersion"] is None]
        raise ValueError("frames mix runs with and without D3 (no D3 in: "
                         f"{mixed}); one training set is one potential-energy surface")
    d3 = "included" if has_d3 == {True} else "none"
    if d3 == "included":
        warns.append("labels include D3: train a model that does NOT add D3 at inference, or "
                     "re-run the frames with d3=\"none\" (D3 forces and stress can't be "
                     "subtracted from the .out)")
    if len({f["source"] for f in frames}) < len(frames):
        warns.append("the same .out appears more than once")
    shas = [f["sha256"] for f in frames]
    if len(set(shas)) < len(shas):
        warns.append("two frames have identical .out files (same sha256)")
    return to_extxyz(frames, cts, head), {
        "frames": len(frames), "natoms": [len(f["symbols"]) for f in frames], "d3": d3,
        "versions": sorted({f["version"] for f in frames if f["version"]}),
        "stress": all(f["stress"] is not None for f in frames), "warnings": warns}
