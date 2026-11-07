"""PLUMED input (plumed.dat): collective variables, a bias, and PRINT.

Taught from the PLUMED inputs that ran: HOKUSAI's LAMMPS-PLUMED smoke test
(~/software/mlip/2026.10/tests/smoke/plumed.dat: DISTANCE + RESTRAINT), and
the PLUMED masterclass 21.4 exercises in plumed_tutorial/ (master-ISDD-1:
GYRATION, COORDINATION, TORSION with MOLINFO; master-ISDD-2: well-tempered
METAD and its reweighting pass). Copies in tests/fixtures/plumed/ and
tests/fixtures/lammps/hokusai_smoke/.

Covers the CVs and biases those used; anything else (CUSTOM, XDISTANCES,
INCLUDE, as in lugano-6a) is not written by this tool.
"""
import re

CV_KEYS = {
    "DISTANCE": {"atoms": "ATOMS", "components": "COMPONENTS"},
    "TORSION": {"atoms": "ATOMS"},
    "ANGLE": {"atoms": "ATOMS"},
    "GYRATION": {"atoms": "ATOMS", "kind": "TYPE"},
    "COORDINATION": {"groupa": "GROUPA", "groupb": "GROUPB", "r_0": "R_0", "d_0": "D_0",
                     "nn": "NN", "mm": "MM"},
}
NATOMS = {"DISTANCE": 2, "TORSION": 4, "ANGLE": 3}
BIAS_COMPONENTS = {"RESTRAINT": {"bias", "force2"}, "MOVINGRESTRAINT": {"bias", "work", "force2"},
                   "METAD": {"bias", "work", "rbias", "rct", "acc"}}
CV_COMPONENTS = {"DISTANCE": {"x", "y", "z"}}
UNIT_KEYS = {"length": "LENGTH", "energy": "ENERGY", "time": "TIME"}


def _atoms(v, where, molinfo, natoms):
    if isinstance(v, str):
        if not v.startswith("@"):
            raise ValueError(f"{where}: atoms is a list of 1-based indices or a MOLINFO "
                             f"shortcut like '@phi-2'; got {v!r}")
        if not molinfo:
            raise ValueError(f"{where}: {v} needs molinfo (a PDB of the system)")
        return v
    if not isinstance(v, list) or not v or not all(isinstance(i, int) and i >= 1 for i in v):
        raise ValueError(f"{where}: atoms is a list of 1-based atom indices (LAMMPS ids)")
    if natoms and max(v) > natoms:
        raise ValueError(f"{where}: atom {max(v)} beyond the {natoms} atoms of the system")
    return ",".join(map(str, v))


def _val(v):
    if isinstance(v, bool):
        return "YES" if v else "NO"
    if isinstance(v, (list, tuple)):
        return ",".join(_val(x) for x in v)
    return str(v)


def make_input(cvs: list[dict], bias: list[dict] | None = None, print_stride: int = 10,
               print_file: str = "COLVAR", print_args: list[str] | None = None,
               units: dict | str | None = None, temperature: float | None = None,
               mode: str = "run", molinfo: str | None = None,
               natoms: int | None = None) -> tuple[str, dict]:
    if mode not in ("run", "reweight"):
        raise ValueError("mode is 'run' (biased MD) or 'reweight' (plumed driver over a "
                         "finished trajectory with the final METAD bias)")
    if not cvs:
        raise ValueError("cvs: at least one, e.g. [{'label': 'd', 'type': 'DISTANCE', "
                         "'atoms': [1, 2]}]")
    lines, labels, comps, warnings = [], [], {}, []
    if units:
        if units == "natural":
            lines.append("UNITS NATURAL")
        elif isinstance(units, dict) and set(units) <= set(UNIT_KEYS):
            lines.append("UNITS " + " ".join(f"{UNIT_KEYS[k]}={v}" for k, v in units.items()))
        else:
            raise ValueError("units is 'natural' or {length, energy, time}, e.g. {'length': 'A'}")
    length = (units or {}).get("length", "nm") if isinstance(units, dict) else (
        "sigma" if units == "natural" else "nm")
    if molinfo:
        lines.append(f"MOLINFO STRUCTURE={molinfo}")

    def label_ok(lab, where):
        if not isinstance(lab, str) or not re.fullmatch(r"[A-Za-z_][\w-]*", lab):
            raise ValueError(f"{where}: label {lab!r} must start with a letter, no dots or spaces")
        if lab in labels:
            raise ValueError(f"{where}: label {lab!r} used twice")
        labels.append(lab)

    def arg_ok(a, where):
        lab, _, comp = a.partition(".")
        if lab not in labels:
            raise ValueError(f"{where}: ARG {a!r} is not a label defined above ({labels})")
        if comp and comp not in comps.get(lab, set()):
            raise ValueError(f"{where}: {lab!r} has no component {comp!r} "
                             f"(it has {sorted(comps.get(lab, set())) or 'none'})")

    for i, cv in enumerate(cvs):
        where = f"cvs[{i}]"
        t = str(cv.get("type", "")).upper()
        if t not in CV_KEYS:
            raise ValueError(f"{where}: type is one of {', '.join(CV_KEYS)}; got {cv.get('type')!r}")
        unknown = sorted(set(cv) - {"label", "type"} - set(CV_KEYS[t]))
        if unknown:
            raise ValueError(f"{where}: {t} takes {sorted(CV_KEYS[t])}; unknown {unknown}")
        label_ok(cv.get("label"), where)
        parts = [f"{cv['label']}: {t}"]
        for k, key in CV_KEYS[t].items():
            if k not in cv:
                continue
            v = cv[k]
            if k in ("atoms", "groupa", "groupb"):
                v = _atoms(v, where, molinfo, natoms)
                if t in NATOMS and isinstance(cv[k], list) and len(cv[k]) != NATOMS[t]:
                    raise ValueError(f"{where}: {t} takes {NATOMS[t]} atoms")
            parts.append(f"{key}={_val(v)}")
        if t == "COORDINATION":
            if "groupa" not in cv or "r_0" not in cv:
                raise ValueError(f"{where}: COORDINATION needs groupa and r_0 (in {length})")
        elif "atoms" not in cv:
            raise ValueError(f"{where}: {t} needs atoms")
        comps[cv["label"]] = CV_COMPONENTS.get(t, set()) if cv.get("components") else set()
        lines.append(" ".join(parts))

    bias = list(bias or [])
    metad = [b for b in bias if str(b.get("type", "")).upper() == "METAD"]
    if mode == "reweight" and len(metad) != 1:
        raise ValueError("reweight needs exactly one METAD bias, the same as the run's")
    if mode == "reweight" and temperature is None:
        raise ValueError("reweight needs temperature (plumed driver has no thermostat)")
    for i, b in enumerate(bias):
        where = f"bias[{i}]"
        t = str(b.get("type", "")).upper()
        if t not in BIAS_COMPONENTS:
            raise ValueError(f"{where}: type is RESTRAINT, MOVINGRESTRAINT or METAD")
        label_ok(b.get("label"), where)
        args = b.get("arg")
        args = [args] if isinstance(args, str) else args
        if not args:
            raise ValueError(f"{where}: arg names the CV(s) to bias")
        for a in args:
            arg_ok(a, where)
        n = len(args)
        if t == "RESTRAINT":
            need, keys = ("at", "kappa"), ("at", "kappa", "slope")
            body = [f"ARG={','.join(args)}"] + [f"{k.upper()}={_val(b[k])}" for k in keys if k in b]
        elif t == "MOVINGRESTRAINT":
            need = ("at", "kappa", "step")
            keys = need
            m = len(b.get("step", []))
            if not m or any(len(b.get(k, [])) != m for k in need):
                raise ValueError(f"{where}: MOVINGRESTRAINT takes step, at and kappa as equal-"
                                 "length lists (one entry per schedule point)")
            if list(b["step"]) != sorted(b["step"]):
                raise ValueError(f"{where}: step must increase")
            body = [f"ARG={','.join(args)}"]
            for j in range(m):
                body += [f"AT{j}={_val(b['at'][j])}", f"KAPPA{j}={_val(b['kappa'][j])}",
                         f"STEP{j}={b['step'][j]}"]
        else:
            need = ("sigma", "height", "pace")
            keys = ("sigma", "height", "pace", "biasfactor", "grid_min", "grid_max", "file")
            if b.get("biasfactor") is not None and b["biasfactor"] <= 1:
                raise ValueError(f"{where}: biasfactor > 1 (well-tempered); omit it for plain METAD")
            if b.get("biasfactor") and temperature is None:
                raise ValueError(f"{where}: well-tempered METAD needs temperature: LAMMPS "
                                 "doesn't pass it to PLUMED")
            if ("grid_min" in b) != ("grid_max" in b):
                raise ValueError(f"{where}: grid_min and grid_max go together")
            for k in ("sigma", "grid_min", "grid_max"):
                if k in b and len(b[k] if isinstance(b[k], list) else [b[k]]) != n:
                    raise ValueError(f"{where}: {k} needs one value per arg ({n})")
            if "grid_min" not in b:
                warnings.append(f"{b['label']}: no grid: every step sums all hills, which "
                                "slows long runs; set grid_min and grid_max")
            if mode == "reweight":
                b = {**b, "pace": 10000000, "height": 0.0}
            body = [f"ARG={','.join(args)}"]
            body += [f"{k.upper()}={_val(b[k])}" for k in keys if k in b and k != "file"]
            body.append(f"FILE={b.get('file', 'HILLS')}")
            if mode == "reweight":
                body.append("RESTART=YES")
            if temperature is not None:
                body.append(f"TEMP={_val(temperature)}")
        miss = [k for k in need if k not in b]
        if miss:
            raise ValueError(f"{where}: {t} needs {list(need)}; missing {miss}")
        comps[b["label"]] = BIAS_COMPONENTS[t]
        if t == "METAD":
            lines.append(f"{b['label']}: METAD ...")
            lines += [f"   {x}" for x in body]
            lines.append("...")
        else:
            lines.append(f"{b['label']}: {t} " + " ".join(body))

    cv_labels = [c["label"] for c in cvs]
    if mode == "reweight":
        pa = cv_labels + [f"{metad[0]['label']}.bias"]
        stride, pfile = 1, print_file if print_file != "COLVAR" else "COLVAR_REWEIGHT"
    else:
        pa = print_args or cv_labels + [f"{b['label']}.bias" for b in bias]
        stride, pfile = print_stride, print_file
    for a in pa:
        arg_ok(a, "print_args")
    lines.append(f"PRINT ARG={','.join(pa)} FILE={pfile} STRIDE={stride}")
    if length == "nm" and any(c.get("type", "").upper() in ("DISTANCE", "COORDINATION", "GYRATION")
                              for c in cvs):
        warnings.append("lengths are in nm (PLUMED's default), also with LAMMPS metal units: "
                        "R_0 and restraint AT values are in nm. Pass units {'length': 'A'} to "
                        "work in A")
    info = {"labels": labels, "print": {"file": pfile, "stride": stride, "args": pa},
            "mode": mode, "length_unit": length,
            "outputs": [pfile] + [b.get("file", "HILLS") for b in metad if mode == "run"],
            "warnings": warnings}
    if mode == "reweight":
        info["driver"] = ("plumed driver --plumed <this file> --ixyz traj.xyz --length-units A "
                          "(with the run's HILLS in the folder; "
                          "then yagura colvar_fes with bias ['" + metad[0]["label"] + ".bias'])")
    return "\n".join(lines) + "\n", info
