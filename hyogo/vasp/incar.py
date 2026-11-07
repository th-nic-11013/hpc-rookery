"""Write a VASP INCAR by editing a base INCAR (G32).

Text in, text out: no file writes, no cluster. Edits keep the base file's
layout and comments: a key that is already there (even commented out) is
replaced in place, a new key goes after its natural neighbour (NUPDOWN and
ISTART after MAGMOM, NBANDS and KPAR after NCORE) or at the end.

What it checks is what cost runs before:
  * MAGMOM must follow the POSCAR species order and count every atom;
  * NBANDS is rounded up by VASP, silently, to a multiple of the number of
    band groups (ranks / (KPAR * NCORE)), so changing NCORE can change the
    band count. A pinned NBANDS that does not divide is refused;
  * NUPDOWN fixes the total moment of the result; a constrained pre-run
    (INCAR.pre) followed by a free run from its WAVECAR does not.
"""
from __future__ import annotations

import re

# a key added to an INCAR that doesn't have it goes after this key's line
AFTER = {"NUPDOWN": "MAGMOM", "ISTART": "MAGMOM", "NBANDS": "NCORE", "KPAR": "NCORE"}
SPIN_KEYS = ("MAGMOM", "NUPDOWN")
PRE_DEFAULTS = {"ediff": "1E-4", "nelm": 120}
KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")


def fmt(value) -> str:
    """An INCAR value: booleans as .TRUE./.FALSE., floats as Python prints
    them (1.0 stays 1.0, 1e-06 is read by VASP)."""
    if isinstance(value, bool):
        return ".TRUE." if value else ".FALSE."
    if isinstance(value, float):
        return repr(value)
    return str(value).strip()


def parse(text: str) -> dict:
    """KEY -> value string of an INCAR, comments dropped; the last setting
    of a key wins (as in VASP). `;` separates several settings on a line."""
    out = {}
    for line in text.splitlines():
        line = re.split(r"[#!]", line, maxsplit=1)[0]
        for part in line.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip().upper()
                if KEY.match(k):
                    out[k] = " ".join(v.split())
    return out


def setkey(text: str, key: str, value: str | None) -> str:
    """Set KEY = value in place (an existing or commented-out line), else
    after AFTER[key]'s line, else at the end. value None removes the key."""
    pat = rf"^[#!\t ]*{key}[\t ]*=.*$"
    if value is None:
        return re.sub(pat + r"\n?", "", text, flags=re.M)
    line = f"{key:<6} = {value}"
    if re.search(pat, text, re.M):
        return re.sub(pat, lambda _: line, text, count=1, flags=re.M)
    after = AFTER.get(key)
    if after and re.search(rf"^\s*{after}\s*=", text, re.M):
        return re.sub(rf"^(\s*{after}\s*=.*)$", lambda m: f"{m.group(1)}\n{line}", text,
                      count=1, flags=re.M)
    return (text.rstrip("\n") + "\n" if text.strip() else "") + line + "\n"


def read_poscar_species(text: str) -> list[tuple[str, int]]:
    """[(element, count), ...] in POSCAR order (VASP 5 format, species line)."""
    lines = text.splitlines()
    if len(lines) < 8:
        raise ValueError("POSCAR is too short to have species and counts lines")
    names, counts = lines[5].split(), lines[6].split()
    if not names or not all(n[0].isalpha() for n in names):
        raise ValueError("POSCAR line 6 has no element symbols (a VASP 4 POSCAR?); "
                         "write the species line first")
    try:
        nums = [int(c) for c in counts[:len(names)]]
    except ValueError:
        raise ValueError("POSCAR line 7 is not a list of atom counts") from None
    if len(nums) != len(names):
        raise ValueError(f"POSCAR names {len(names)} species but counts {len(nums)}")
    # "Cr_pv" or "Cr/abc" style labels: element is the leading letters
    return [(re.match(r"[A-Z][a-z]?", n).group(0), c) for n, c in zip(names, nums)]


def expand_magmom(value: str) -> list[float]:
    """MAGMOM's per-atom list ("12*0 24*3.0 1.5")."""
    out = []
    for tok in value.split():
        if "*" in tok:
            n, m = tok.split("*", 1)
            out += [float(m)] * int(n)
        else:
            out.append(float(tok))
    return out


def compress_magmom(moments: list[float]) -> str:
    """Run-length MAGMOM value: [0]*12 + [3]*24 -> "12*0 24*3"."""
    parts = []
    for m in moments:
        if parts and parts[-1][1] == m:
            parts[-1][0] += 1
        else:
            parts.append([1, m])
    return " ".join(f"{n}*{m:g}" for n, m in parts)


def magmom_list(species: list[tuple[str, int]], per_element: dict | None,
                sites: dict | None) -> list[float]:
    """Per-atom moments: one value per element (missing ones 0), then
    per-site overrides keyed by 1-based atom index."""
    per_element = per_element or {}
    known = {el for el, _ in species}
    unknown = sorted(set(per_element) - known)
    if unknown:
        raise ValueError(f"magmom names {unknown}, not in the POSCAR species {sorted(known)}")
    moms = [float(per_element.get(el, 0.0)) for el, n in species for _ in range(n)]
    for k, m in (sites or {}).items():
        i = int(k)
        if not 1 <= i <= len(moms):
            raise ValueError(f"magmom_sites index {i} is outside 1..{len(moms)} (1-based)")
        moms[i - 1] = float(m)
    return moms


def nbands_from_outcar(text: str) -> int:
    """NBANDS a finished or running OUTCAR used (NUL bytes already stripped)."""
    m = re.search(r"NBANDS=\s*(\d+)", text)
    if not m:
        raise ValueError("no NBANDS= line in that OUTCAR (did the run start?)")
    return int(m.group(1))


def make_incar(base: str, poscar: str, settings: dict | None = None,
               magmom: dict | None = None, magmom_sites: dict | None = None,
               nupdown: float | None = None, parallel: dict | None = None,
               nbands: int | None = None, pre: dict | None = None) -> tuple[str, str | None, dict]:
    """Return (INCAR text, INCAR.pre text or None, info)."""
    species = read_poscar_species(poscar)
    natoms = sum(n for _, n in species)
    old = parse(base)
    text = base if base.endswith("\n") or not base else base + "\n"
    warnings = []

    for k, v in (settings or {}).items():
        k2 = k.strip().upper()
        if not KEY.match(k2):
            raise ValueError(f"settings key {k!r} is not an INCAR tag")
        if k2 in ("MAGMOM", "NUPDOWN", "NBANDS", "NCORE", "KPAR") and v is not None:
            raise ValueError(f"set {k2} with its own argument (magmom, nupdown, parallel, "
                             "nbands), which checks it")
        text = setkey(text, k2, None if v is None else fmt(v))

    if magmom is not None or magmom_sites:
        text = setkey(text, "MAGMOM", compress_magmom(magmom_list(species, magmom, magmom_sites)))
    if nupdown is not None:
        text = setkey(text, "NUPDOWN", fmt(nupdown))

    # parallel layout and NBANDS
    groups = None
    if parallel:
        bad = set(parallel) - {"ranks", "ncore", "kpar"}
        if bad:
            raise ValueError(f"parallel takes ranks, ncore and kpar, not {sorted(bad)}")
        ranks = int(parallel.get("ranks") or 0)
        ncore = int(parallel.get("ncore") or parse(text).get("NCORE", 1))
        kpar = int(parallel.get("kpar") or parse(text).get("KPAR", 1))
        if "ncore" in parallel:
            text = setkey(text, "NCORE", str(ncore))
        if "kpar" in parallel:
            text = setkey(text, "KPAR", str(kpar))
        if ranks:
            if ranks % (ncore * kpar):
                raise ValueError(f"{ranks} ranks don't divide into KPAR {kpar} x NCORE {ncore}")
            groups = ranks // (ncore * kpar)
    if nbands is not None:
        if groups and nbands % groups:
            up = -(-nbands // groups) * groups
            raise ValueError(f"NBANDS {nbands} is not a multiple of the {groups} band groups "
                             f"(ranks / (KPAR x NCORE)); VASP would silently run {up} bands. "
                             "Change NCORE or the ranks, or pin a multiple")
        text = setkey(text, "NBANDS", str(nbands))
    elif groups and "NBANDS" not in parse(text):
        warnings.append(f"NBANDS is not pinned: VASP rounds its default up to a multiple of the "
                        f"{groups} band groups, so outputs run with another NCORE, KPAR or rank "
                        "count may use a different band count. Pin it (nbands or nbands_from) "
                        "when comparing against them")
    elif "NBANDS" in parse(text) and groups and int(parse(text)["NBANDS"]) % groups:
        n = int(parse(text)["NBANDS"])
        raise ValueError(f"the INCAR's NBANDS {n} is not a multiple of the {groups} band groups; "
                         f"VASP would silently run {-(-n // groups) * groups}")

    # constrain-then-release: INCAR.pre fixes NUPDOWN and writes WAVECAR;
    # the main run reads it (ISTART 1) with the moment free
    pre_text = None
    if pre is not None:
        if "nupdown" not in pre:
            raise ValueError("pre needs nupdown: the total moment the pre-run is held at")
        bad = set(pre) - {"nupdown", "ediff", "nelm"}
        if bad:
            raise ValueError(f"pre takes nupdown, ediff and nelm, not {sorted(bad)}")
        if nupdown is not None:
            raise ValueError("pre releases the moment in the main run; don't also fix nupdown "
                             "there (use nupdown alone for a fixed-moment result)")
        if "NUPDOWN" in parse(text) and float(parse(text)["NUPDOWN"]) != -1:
            text = setkey(text, "NUPDOWN", None)
            warnings.append("removed the base INCAR's NUPDOWN from the main run: with pre, "
                            "the main run's moment is free")
        p = {**PRE_DEFAULTS, **pre}
        pre_text = setkey(text, "NUPDOWN", fmt(p["nupdown"]))
        pre_text = setkey(pre_text, "EDIFF", fmt(p["ediff"]))
        pre_text = setkey(pre_text, "NELM", fmt(p["nelm"]))
        pre_text = setkey(pre_text, "LWAVE", ".TRUE.")
        text = setkey(text, "ISTART", "1")

    final = parse(text)
    # checks on the INCAR as written
    spin = final.get("ISPIN", "1")
    if spin != "2" and any(k in final for k in SPIN_KEYS):
        raise ValueError(f"MAGMOM or NUPDOWN with ISPIN {spin}: VASP ignores them. "
                         "Set ISPIN = 2 (settings={'ISPIN': 2})")
    moms = None
    if "MAGMOM" in final:
        try:
            moms = expand_magmom(final["MAGMOM"])
        except ValueError:
            raise ValueError(f"can't read MAGMOM = {final['MAGMOM']}") from None
        if len(moms) != natoms:
            raise ValueError(f"MAGMOM has {len(moms)} values, the POSCAR {natoms} atoms "
                             "(it must follow the POSCAR species order)")
    if "NUPDOWN" in final and pre is None and float(final["NUPDOWN"]) != -1:
        warnings.append(f"NUPDOWN {final['NUPDOWN']} fixes the total moment of this result. "
                        "Right for a half-metal (integer moment) or a non-magnetic system; "
                        "for a metallic magnet it biases the label: use pre to constrain, "
                        "then release")
    if spin == "2" and int(final.get("LORBIT", "0") or 0) < 10:
        warnings.append("LORBIT < 10: the OUTCAR will have no per-site moments "
                        "(magnetization (x) block); set LORBIT 10 to check spin states")
    if pre is not None:
        warnings.append("the main INCAR has ISTART 1: run INCAR.pre first (in its own folder), "
                        "then copy its WAVECAR into the main run's folder; without the WAVECAR "
                        "VASP starts from scratch with no constraint")
    if "NELM" not in final:
        warnings.append("NELM not set: VASP's default is 60, and a run that hits NELM still "
                        "exits 0 (vasp_parse_output reports it)")

    changed = {k: [old.get(k), final.get(k)] for k in sorted(set(old) | set(final))
               if old.get(k) != final.get(k)}
    info = {"natoms": natoms, "species": [[el, n] for el, n in species],
            "magmom": final.get("MAGMOM"),
            "total_magmom": round(sum(moms), 6) if moms is not None else None,
            "nbands": int(final["NBANDS"]) if "NBANDS" in final else None,
            "band_groups": groups, "changed": changed,
            "pre": {"changed": {k: [final.get(k), v] for k, v in parse(pre_text).items()
                                if final.get(k) != v}} if pre_text else None,
            "warnings": warnings}
    return text, pre_text, info
