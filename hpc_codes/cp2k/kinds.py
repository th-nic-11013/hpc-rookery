"""The CP2K &KIND facts of a build: valence q, basis and GTH potential per element.

    python3 -I kinds.py DATA_DIR BASIS ELEMENT [ELEMENT ...]

Reads DATA_DIR/BASIS_MOLOPT for the BASIS entry of each element and its
-qN alias (the valence count), then checks that DATA_DIR/GTH_POTENTIALS
has GTH-PBE-qN for it, so q is never guessed (Zr q12, Ti q12, Cu q11, N q5).
Prints one JSON object: {"kinds": {element: {q, basis, potential}},
"errors": {element: message}}. Standard library only, so it runs on any
login node; cp2k_kinds sends it there, next to the build's data folder.
"""
import json
import re
import sys
from pathlib import Path


def kinds(data: Path, basis: str, elements: list[str]) -> dict:
    out, errors = {}, {}
    try:
        basis_lines = (data / "BASIS_MOLOPT").read_text().splitlines()
        pot_lines = (data / "GTH_POTENTIALS").read_text().splitlines()
    except OSError as e:
        return {"kinds": {}, "errors": {"*": f"cannot read the data folder: {e}"}}
    for element in dict.fromkeys(elements):
        q, found = None, False
        for line in basis_lines:
            t = line.split()
            if len(t) >= 2 and t[0] == element and basis in t[1:]:
                found = True
                for tok in t[1:]:
                    m = re.fullmatch(re.escape(basis) + r"-q(\d+)", tok)
                    if m:
                        q = int(m.group(1))
                break
        if not found:
            errors[element] = f"no {basis} for {element} in {data}/BASIS_MOLOPT"
            continue
        if q is None:
            errors[element] = f"{element} {basis}: found, but no -qN alias to read the valence from"
            continue
        pot = f"GTH-PBE-q{q}"
        if not any(l.split()[:2] == [element, pot] for l in pot_lines):
            errors[element] = f"no {element} {pot} in {data}/GTH_POTENTIALS"
            continue
        out[element] = {"q": q, "basis": basis, "potential": pot}
    return {"kinds": out, "errors": errors}


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit("usage: kinds.py DATA_DIR BASIS ELEMENT [ELEMENT ...]")
    print(json.dumps(kinds(Path(sys.argv[1]).expanduser(), sys.argv[2], sys.argv[3:])))
