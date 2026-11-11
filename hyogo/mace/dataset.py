"""Labelled extended-XYZ data sets for MLIP training: read, profile, check.

Text in, dicts out: no file I/O, no ASE. Frames are kept as their own text
blocks, so a split writes each frame byte for byte as it was labelled
(hyogo's cp2k_make_labels puts the source .out and its sha256 on every
frame; re-serialising would be one more thing to trust).

Adapted from the user's fine-tuning tools (mcp_tools/data_tools.py:
profile_frames, validate_dataset_compatibility; combine_relabelled.py's
missing-key checks). Their REF_energy -> energy copy is not carried: the
key names are passed to MACE instead (MACE itself warns that the plain
`energy`/`forces` keys are unsafe with ASE >= 3.23).
"""
import re
from collections import Counter

# periodic table up to Z = 94, for atomic numbers (E0s files are keyed by Z)
_SYMBOLS = ("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
            "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce "
            "Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn "
            "Fr Ra Ac Th Pa U Np Pu").split()
Z_OF = {s: i + 1 for i, s in enumerate(_SYMBOLS)}

_INFO = re.compile(r'([A-Za-z_][\w.\-]*)\s*=\s*("([^"]*)"|\'([^\']*)\'|\S+)')
ENERGY_KEYS = ("REF_energy", "energy", "dft_energy", "TotEnergy")
FORCES_KEYS = ("REF_forces", "forces", "dft_force", "force")
STRESS_KEYS = ("REF_stress", "stress", "dft_stress")


def _info(comment: str) -> dict:
    out = {}
    for m in _INFO.finditer(comment):
        v = m.group(3) if m.group(3) is not None else m.group(4) if m.group(4) is not None else m.group(2)
        out[m.group(1)] = v
    return out


def _columns(properties: str) -> list[tuple[str, str, int]]:
    p = properties.split(":")
    if len(p) % 3:
        raise ValueError(f"Properties={properties!r} is not name:type:count triples")
    return [(p[i], p[i + 1], int(p[i + 2])) for i in range(0, len(p), 3)]


def read_frames(text: str, source: str = "") -> list[dict]:
    """Split extxyz text into frames: {"text" (the frame's own lines),
    "natoms", "info" (comment-line key=value, strings), "arrays" (per-atom
    column names), "symbols", "source", "index"}."""
    lines, frames, i = text.splitlines(), [], 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        try:
            n = int(lines[i].split()[0])
        except ValueError:
            raise ValueError(f"{source or 'extxyz'} line {i + 1}: expected an atom count, got "
                             f"{lines[i][:40]!r}") from None
        if i + 2 + n > len(lines):
            raise ValueError(f"{source or 'extxyz'}: frame {len(frames)} is cut short "
                             f"({n} atoms announced at line {i + 1})")
        info = _info(lines[i + 1])
        cols = _columns(info["Properties"]) if "Properties" in info else [("species", "S", 1), ("pos", "R", 3)]
        atom_lines = lines[i + 2:i + 2 + n]
        at = next((k for k, (name, _, _) in enumerate(cols) if name == "species"), None)
        offset = sum(c for _, _, c in cols[:at]) if at is not None else 0
        symbols = [l.split()[offset] for l in atom_lines]
        frames.append({"text": "\n".join(lines[i:i + 2 + n]) + "\n", "natoms": n, "info": info,
                       "arrays": [name for name, _, _ in cols], "symbols": symbols,
                       "source": source, "index": len(frames)})
        i += 2 + n
    return frames


def pick_keys(frames: list[dict], energy_key=None, forces_key=None, stress_key=None) -> dict:
    """The label keys to hand MACE: given ones, else the first known name
    present in every frame. stress is None when not every frame has one."""
    def first(names, where):
        return next((k for k in names if all(k in f[where] for f in frames)), None)
    return {"energy_key": energy_key or first(ENERGY_KEYS, "info"),
            "forces_key": forces_key or first(FORCES_KEYS, "arrays"),
            "stress_key": stress_key or first(STRESS_KEYS, "info")}


def profile(frames: list[dict]) -> dict:
    if not frames:
        raise ValueError("no frames")
    natoms = [f["natoms"] for f in frames]
    species = Counter(s for f in frames for s in f["symbols"])
    unknown = sorted(set(species) - set(Z_OF))
    if unknown:
        raise ValueError(f"element symbols not recognised: {unknown}")
    info_keys, arrays = Counter(), Counter()
    for f in frames:
        info_keys.update(list(f["info"]))
        arrays.update(f["arrays"])
    n = len(frames)
    return {"frames": n,
            "natoms": {"min": min(natoms), "max": max(natoms), "unique": sorted(set(natoms))},
            "species": sorted(species, key=Z_OF.get),
            "atomic_numbers": sorted(Z_OF[s] for s in species),
            "species_counts": {s: species[s] for s in sorted(species, key=Z_OF.get)},
            "info_keys": {k: c for k, c in sorted(info_keys.items())},
            "arrays": {k: c for k, c in sorted(arrays.items())},
            "config_types": dict(Counter(f["info"].get("config_type", "") for f in frames)),
            "heads": dict(Counter(f["info"].get("head", "") for f in frames)),
            "periodic": sum("Lattice" in f["info"] for f in frames)}


def check(frames: list[dict], energy_key=None, forces_key=None, stress_key=None,
          allowed_species: list[str] | None = None) -> dict:
    """Profile plus the problems that make a set unfit to train on, and
    warnings. Never raises on a bad set: `ok` is False and `problems` say why."""
    prof = profile(frames)
    keys = pick_keys(frames, energy_key, forces_key, stress_key)
    problems, warnings, n = [], [], len(frames)

    def missing(key, where):
        return [f["index"] for f in frames if key not in f[where]]

    for label, key, where in (("energy", keys["energy_key"], "info"), ("forces", keys["forces_key"], "arrays")):
        if key is None:
            problems.append(f"no {label} key is present in every frame (looked for "
                            f"{', '.join(ENERGY_KEYS if label == 'energy' else FORCES_KEYS)}); "
                            f"pass {label}_key")
        elif missing(key, where):
            m = missing(key, where)
            problems.append(f"{label} key {key!r} is missing in {len(m)} of {n} frames (first: {m[:5]})")
    if stress_key and missing(stress_key, "info"):
        m = missing(stress_key, "info")
        problems.append(f"stress key {stress_key!r} is missing in {len(m)} of {n} frames (first: {m[:5]})")
    if keys["stress_key"] is None:
        some = [k for k in STRESS_KEYS if prof["info_keys"].get(k)]
        warnings.append("no stress in every frame" + (f" ({some[0]} is in only "
                        f"{prof['info_keys'][some[0]]} of {n})" if some else "")
                        + ": the model will be trained on energy and forces only")
    elif prof["periodic"] < n:
        problems.append(f"{n - prof['periodic']} frames have stress but no Lattice")
    if keys["energy_key"] in ("energy", "forces") or keys["forces_key"] in ("energy", "forces"):
        warnings.append("label keys named plain `energy`/`forces` are unsafe with ASE >= 3.23 (MACE "
                        "warns and may read no label); REF_energy / REF_forces are the safe names")
    # one training set is one potential-energy surface (cp2k_make_labels writes dispersion_eV
    # only on frames whose run included D3)
    d3 = sum("dispersion_eV" in f["info"] for f in frames)
    if 0 < d3 < n:
        problems.append(f"{d3} of {n} frames carry dispersion_eV (labels with D3) and {n - d3} do not: "
                        "D3 and D3-free labels are mixed")
    shas = Counter(f["info"]["source_sha256"] for f in frames if "source_sha256" in f["info"])
    dup = sum(c - 1 for c in shas.values() if c > 1)
    if dup:
        warnings.append(f"{dup} frame(s) repeat another frame's source_sha256 (same .out twice)")
    if allowed_species is not None:
        extra = sorted(set(prof["species"]) - set(allowed_species), key=Z_OF.get)
        if extra:
            problems.append(f"elements the foundation model does not cover: {extra}")
    if n < 2:
        problems.append("fewer than 2 frames: no train/validation split is possible")
    return {"ok": not problems, "problems": problems, "warnings": warnings, "keys": keys,
            "d3": "included" if d3 == n else "none" if d3 == 0 else "mixed", "profile": prof}
