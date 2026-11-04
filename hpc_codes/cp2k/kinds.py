"""The CP2K &KIND facts of a build: valence q, basis and GTH potential per element.

    python3 -I kinds.py [--family F] [--aux AUX] DATA_DIR BASIS ELEMENT [ELEMENT ...]

Family "molopt" (the default) reads DATA_DIR/BASIS_MOLOPT for the BASIS
entry of each element and its -qN alias (the valence count), then checks
that DATA_DIR/GTH_POTENTIALS has GTH-PBE-qN for it, so q is never guessed
(Zr q12, Ti q12, Cu q11, N q5). The UZH families ("uzh-pbe", "uzh-scan",
"uzh-pbe0") read the newest BASIS_MOLOPT_UZH* and POTENTIAL_UZH* instead,
where q is part of the basis name (DZVP-MOLOPT-SCAN-GTH-q12), and need the
potential of the same family (GTH-SCAN-q12). --aux adds an ADMM auxiliary
basis from BASIS_ADMM_UZH (admm-dzp -> admm-dzp-q12).
Prints one JSON object: {"files": {"basis": [...], "potential": name},
"kinds": {element: {q, basis, potential[, aux_basis]}}, "errors": {element:
message}}. Standard library only, so it runs on any login node; cp2k_kinds
sends it there, next to the build's data folder.
"""
import argparse
import json
import re
from pathlib import Path

# family -> (basis file prefix, potential file prefix, potential name stem, default basis)
FAMILIES = {
    "molopt": ("BASIS_MOLOPT", "GTH_POTENTIALS", "GTH-PBE", "DZVP-MOLOPT-SR-GTH"),
    "uzh-pbe": ("BASIS_MOLOPT_UZH", "POTENTIAL_UZH", "GTH-PBE", "DZVP-MOLOPT-PBE-GTH"),
    "uzh-scan": ("BASIS_MOLOPT_UZH", "POTENTIAL_UZH", "GTH-SCAN", "DZVP-MOLOPT-SCAN-GTH"),
    "uzh-pbe0": ("BASIS_MOLOPT_UZH", "POTENTIAL_UZH", "GTH-PBE0", "DZVP-MOLOPT-PBE0-GTH"),
}
ADMM_FILE = "BASIS_ADMM_UZH"


def newest(data: Path, prefix: str) -> str:
    """PREFIX itself for molopt; for UZH the highest PREFIX_<version>, else PREFIX."""
    if not prefix.endswith("_UZH"):
        return prefix
    versioned = [p.name for p in data.glob(prefix + "_*")
                 if re.fullmatch(re.escape(prefix) + r"_\d+(\.\d+)*", p.name)]
    if not versioned:
        return prefix
    return max(versioned, key=lambda n: [int(v) for v in n[len(prefix) + 1:].split(".")])


def kinds(data: Path, basis: str, elements: list[str], family: str = "molopt",
          aux: str | None = None) -> dict:
    basis_prefix, pot_prefix, stem, _ = FAMILIES[family]
    basis_file, pot_file = newest(data, basis_prefix), newest(data, pot_prefix)
    files = {"basis": [basis_file] + ([ADMM_FILE] if aux else []), "potential": pot_file}
    out, errors = {}, {}
    try:
        basis_lines = (data / basis_file).read_text().splitlines()
        pot_lines = (data / pot_file).read_text().splitlines()
        aux_lines = (data / ADMM_FILE).read_text().splitlines() if aux else []
    except OSError as e:
        return {"files": files, "kinds": {}, "errors": {"*": f"cannot read the data folder: {e}"}}
    for element in dict.fromkeys(elements):
        q, found, name = None, False, basis
        for line in basis_lines:
            t = line.split()
            if len(t) < 2 or t[0] != element:
                continue
            if family == "molopt":
                if basis not in t[1:]:
                    continue
                found = True
                for tok in t[1:]:
                    m = re.fullmatch(re.escape(basis) + r"-q(\d+)", tok)
                    if m:
                        q = int(m.group(1))
                break
            m = re.fullmatch(re.escape(basis) + r"-q(\d+)", t[1])
            if m:
                found, q, name = True, int(m.group(1)), t[1]
                break
        if not found:
            errors[element] = f"no {basis} for {element} in {data}/{basis_file}"
            continue
        if q is None:
            errors[element] = f"{element} {basis}: found, but no -qN alias to read the valence from"
            continue
        pot = f"{stem}-q{q}"
        if not any(l.split()[:2] == [element, pot] for l in pot_lines):
            errors[element] = f"no {element} {pot} in {data}/{pot_file}"
            continue
        kind = {"q": q, "basis": name, "potential": pot}
        if aux:
            aux_name = f"{aux}-q{q}"
            if not any(l.split()[:2] == [element, aux_name] for l in aux_lines):
                errors[element] = f"no {element} {aux_name} in {data}/{ADMM_FILE}"
                continue
            kind["aux_basis"] = aux_name
        out[element] = kind
    return {"files": files, "kinds": out, "errors": errors}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="molopt", choices=sorted(FAMILIES))
    ap.add_argument("--aux")
    ap.add_argument("data_dir")
    ap.add_argument("basis")
    ap.add_argument("elements", nargs="+")
    a = ap.parse_args()
    print(json.dumps(kinds(Path(a.data_dir).expanduser(), a.basis, a.elements, a.family, a.aux)))
