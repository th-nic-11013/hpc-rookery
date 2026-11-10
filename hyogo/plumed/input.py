"""PLUMED input (plumed.dat): collective variables, a bias, and PRINT.

Taught from the PLUMED inputs that ran: HOKUSAI's LAMMPS-PLUMED smoke test
(~/software/mlip/2026.10/tests/smoke/plumed.dat: DISTANCE + RESTRAINT), and
the PLUMED masterclass 21.4 exercises in plumed_tutorial/ (master-ISDD-1:
GYRATION, COORDINATION, TORSION with MOLINFO; master-ISDD-2: well-tempered
METAD and its reweighting pass). Copies in tests/fixtures/plumed/ and
tests/fixtures/lammps/hokusai_smoke/.

Covers the CVs and biases those used; anything else (CUSTOM, XDISTANCES,
INCLUDE, as in lugano-6a) is not written by this tool.

OPES (G40) was added from the UiO-66 O1 run on HOKUSAI (2026-11-08,
~/agent/work/mlmd-uio66/o1-opes-smoke/plumed.dat, copied to
tests/fixtures/plumed/o1-opes-expanded/): ENERGY and VOLUME CVs,
ECV_MULTITHERMAL_MULTIBARIC and OPES_EXPANDED. ECV_MULTITHERMAL,
OPES_METAD and OPES_METAD_EXPLORE follow the PLUMED 2.10 manual and the
2.10.1 source (src/opes/); they have not been run here yet.
"""
import re

CV_KEYS = {
    "DISTANCE": {"atoms": "ATOMS", "components": "COMPONENTS"},
    "TORSION": {"atoms": "ATOMS"},
    "ANGLE": {"atoms": "ATOMS"},
    "GYRATION": {"atoms": "ATOMS", "kind": "TYPE"},
    "COORDINATION": {"groupa": "GROUPA", "groupb": "GROUPB", "r_0": "R_0", "d_0": "D_0",
                     "nn": "NN", "mm": "MM"},
    "ENERGY": {},   # the MD engine's potential energy (LAMMPS passes it)
    "VOLUME": {},   # the box volume
}
NO_ATOMS = {"ENERGY", "VOLUME"}
NATOMS = {"DISTANCE": 2, "TORSION": 4, "ANGLE": 3}
BIAS_COMPONENTS = {"RESTRAINT": {"bias", "force2"}, "MOVINGRESTRAINT": {"bias", "work", "force2"},
                   "METAD": {"bias", "work", "rbias", "rct", "acc"},
                   "OPES_METAD": {"bias", "rct", "zed", "neff", "nker"},
                   "OPES_METAD_EXPLORE": {"bias", "rct", "zed", "neff", "nker"},
                   "OPES_EXPANDED": {"bias"}}
OPES_METAD_KEYS = ("pace", "barrier", "sigma", "biasfactor", "file")
OPES_EXPANDED_KEYS = ("pace", "observation_steps", "file", "print_stride")
ECV_KEYS = {"ECV_MULTITHERMAL": ("temp_min", "temp_max", "temp_steps"),
            "ECV_MULTITHERMAL_MULTIBARIC": ("temp_min", "temp_max", "temp_steps", "pressure_bar",
                                            "pressure_min_bar", "pressure_max_bar",
                                            "pressure_steps", "cut_corner")}
# PLUMED's ENERGY units in J/mol per unit, LENGTH units in m: for converting a
# pressure in bar to PLUMED's energy/length^3 (PRESSURE has no unit of its own)
ENERGY_J_PER_MOL = {"kj/mol": 1e3, "j/mol": 1.0, "kcal/mol": 4184.0, "ev": 96485.33212331001}
LENGTH_M = {"nm": 1e-9, "a": 1e-10, "um": 1e-6}
AVOGADRO = 6.02214076e23
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


def bar_to_plumed(p_bar: float, units: dict | str | None) -> float:
    """A pressure in bar in PLUMED's energy/length^3 for these UNITS."""
    u = units if isinstance(units, dict) else {}
    if units == "natural":
        raise ValueError("pressures in bar need real units, not 'natural'")
    e = str(u.get("energy", "kj/mol")).lower()
    ln = str(u.get("length", "nm")).lower()
    if e not in ENERGY_J_PER_MOL or ln not in LENGTH_M:
        raise ValueError(f"can't convert a pressure to energy {e!r} / length {ln!r}; use energy "
                         f"{sorted(ENERGY_J_PER_MOL)} and length {sorted(LENGTH_M)}")
    joule = ENERGY_J_PER_MOL[e] / AVOGADRO
    return p_bar * 1e5 * LENGTH_M[ln] ** 3 / joule


def _p(x: float) -> str:
    # 5 significant figures, as the hand-written O1 input (1 bar in eV/A^3 = 6.2415e-07)
    return f"{x:.5g}"


def make_input(cvs: list[dict], bias: list[dict] | None = None, print_stride: int = 10,
               print_file: str = "COLVAR", print_args: list[str] | None = None,
               units: dict | str | None = None, temperature: float | None = None,
               mode: str = "run", molinfo: str | None = None,
               natoms: int | None = None, ecvs: list[dict] | None = None) -> tuple[str, dict]:
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
        elif t not in NO_ATOMS and "atoms" not in cv:
            raise ValueError(f"{where}: {t} needs atoms")
        comps[cv["label"]] = CV_COMPONENTS.get(t, set()) if cv.get("components") else set()
        lines.append(" ".join(parts))

    cv_types = {cv["label"]: str(cv["type"]).upper() for cv in cvs}
    ecv_labels = []
    for i, e in enumerate(ecvs or []):
        where = f"ecvs[{i}]"
        t = str(e.get("type", "")).upper()
        if t not in ECV_KEYS:
            raise ValueError(f"{where}: type is one of {', '.join(ECV_KEYS)}; got {e.get('type')!r}")
        unknown = sorted(set(e) - {"label", "type", "arg"} - set(ECV_KEYS[t]))
        if unknown:
            raise ValueError(f"{where}: {t} takes arg and {list(ECV_KEYS[t])}; unknown {unknown}")
        label_ok(e.get("label"), where)
        args = e.get("arg")
        args = [args] if isinstance(args, str) else list(args or [])
        want = ["ENERGY"] if t == "ECV_MULTITHERMAL" else ["ENERGY", "VOLUME"]
        if [cv_types.get(a) for a in args] != want:
            raise ValueError(f"{where}: {t} takes arg = the label of " + " then ".join(
                f"a {w} CV" for w in want) + f", in that order (PLUMED multiplies the first by "
                f"the temperature terms" + (" and the second by the pressure terms" if len(want) == 2
                                            else "") + f"); got {args}")
        if temperature is None:
            raise ValueError(f"{where}: {t} needs temperature: the thermostat's T (TEMP), which "
                             "must equal the MD engine's (LAMMPS doesn't pass it to PLUMED)")
        need = ["temp_min", "temp_max"] + (["pressure_bar", "pressure_min_bar", "pressure_max_bar"]
                                           if len(want) == 2 else [])
        miss = [k for k in need if k not in e]
        if miss:
            raise ValueError(f"{where}: {t} needs {need}; missing {miss}")
        if not e["temp_min"] < e["temp_max"]:
            raise ValueError(f"{where}: temp_min < temp_max")
        if not e["temp_min"] <= temperature <= e["temp_max"]:
            warnings.append(f"{e['label']}: the thermostat T {temperature} K is outside "
                            f"{e['temp_min']}–{e['temp_max']} K; PLUMED allows it, but sampling "
                            "starts far from every target")
        body = [f"ARG={','.join(args)}", f"TEMP={_val(temperature)}"]
        if len(want) == 2:
            if not e["pressure_min_bar"] < e["pressure_max_bar"]:
                raise ValueError(f"{where}: pressure_min_bar < pressure_max_bar")
            body.append(f"PRESSURE={_p(bar_to_plumed(e['pressure_bar'], units))}")
        body += [f"TEMP_MIN={_val(e['temp_min'])}", f"TEMP_MAX={_val(e['temp_max'])}"]
        if len(want) == 2:
            body += [f"PRESSURE_MIN={_p(bar_to_plumed(e['pressure_min_bar'], units))}",
                     f"PRESSURE_MAX={_p(bar_to_plumed(e['pressure_max_bar'], units))}"]
        for k in ("temp_steps", "pressure_steps"):
            if k in e:
                body.append(f"{k.upper()}={int(e[k])}")
        if e.get("cut_corner") is not None:
            cc = e["cut_corner"]
            if not (isinstance(cc, (list, tuple)) and len(cc) == 4):
                raise ValueError(f"{where}: cut_corner is [temp_low_K, pressure_low_bar, "
                                 "temp_high_K, pressure_high_bar]: two points of the line below "
                                 "which (high T, low P) grid points are dropped")
            body.append(f"CUT_CORNER={_val(cc[0])},{_p(bar_to_plumed(cc[1], units))},"
                        f"{_val(cc[2])},{_p(bar_to_plumed(cc[3], units))}")
        ecv_labels.append(e["label"])
        lines.append(f"{e['label']}: {t} " + " ".join(body))

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
            raise ValueError(f"{where}: type is one of {', '.join(BIAS_COMPONENTS)}")
        label_ok(b.get("label"), where)
        args = b.get("arg")
        args = [args] if isinstance(args, str) else args
        if not args:
            raise ValueError(f"{where}: arg names the CV(s) to bias")
        if t == "OPES_EXPANDED":
            bad = [a for a in args if a not in ecv_labels]
            if bad:
                raise ValueError(f"{where}: OPES_EXPANDED's arg names ECVs from ecvs "
                                 f"({ecv_labels or 'none given'}); not {bad}")
        else:
            for a in args:
                if a.partition(".")[0] in ecv_labels:
                    raise ValueError(f"{where}: {a!r} is an ECV; only OPES_EXPANDED takes ECVs")
                arg_ok(a, where)
        n = len(args)
        unknown = []
        if t in ("OPES_METAD", "OPES_METAD_EXPLORE"):
            unknown = sorted(set(b) - {"label", "type", "arg"} - set(OPES_METAD_KEYS))
        elif t == "OPES_EXPANDED":
            unknown = sorted(set(b) - {"label", "type", "arg"} - set(OPES_EXPANDED_KEYS))
        if unknown:
            raise ValueError(f"{where}: {t} takes arg and "
                             f"{list(OPES_EXPANDED_KEYS if t == 'OPES_EXPANDED' else OPES_METAD_KEYS)}"
                             f"; unknown {unknown}")
        if t == "OPES_EXPANDED":
            need = ("pace",)
            body = [f"ARG={','.join(a + '.*' for a in args)}", f"PACE={int(b['pace'])}"] if "pace" in b else []
            if "observation_steps" in b:
                body.append(f"OBSERVATION_STEPS={int(b['observation_steps'])}")
            body.append(f"FILE={b.get('file', 'DELTAFS')}")
            if "print_stride" in b:
                body.append(f"PRINT_STRIDE={int(b['print_stride'])}")
            if "pace" in b:
                ps = int(b.get("print_stride", 100)) * int(b["pace"])
                warnings.append(f"{b['label']}: DELTAFS is written every PRINT_STRIDE x PACE = {ps} "
                                "steps (PRINT_STRIDE counts in PACE units, default 100); a shorter "
                                "run writes it once")
        elif t in ("OPES_METAD", "OPES_METAD_EXPLORE"):
            need = ("pace", "barrier")
            if temperature is None:
                raise ValueError(f"{where}: {t} needs temperature: LAMMPS doesn't pass it to PLUMED")
            if b.get("biasfactor") is not None and b["biasfactor"] <= 1:
                raise ValueError(f"{where}: biasfactor > 1 (default BARRIER/kT)")
            if "sigma" in b and len(b["sigma"] if isinstance(b["sigma"], list) else [b["sigma"]]) != n:
                raise ValueError(f"{where}: sigma needs one value per arg ({n})")
            body = [f"ARG={','.join(args)}"]
            body += [f"{k.upper()}={_val(b[k])}" for k in OPES_METAD_KEYS if k in b and k != "file"]
            body += [f"FILE={b.get('file', 'KERNELS')}", f"TEMP={_val(temperature)}"]
            if "sigma" not in b:
                warnings.append(f"{b['label']}: no sigma: OPES adapts the kernel width from the "
                                "CV fluctuations (fine for a first run)")
        elif t == "RESTRAINT":
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
        elif t.startswith("OPES"):
            lines.append(f"{b['label']}: {t} " + " ".join(body))
        else:
            lines.append(f"{b['label']}: {t} " + " ".join(body))

    used = {a for b in bias if str(b.get("type", "")).upper() == "OPES_EXPANDED"
            for a in ([b["arg"]] if isinstance(b["arg"], str) else b["arg"])}
    if set(ecv_labels) - used:
        raise ValueError(f"ecvs {sorted(set(ecv_labels) - used)} are not used by an OPES_EXPANDED "
                         "bias; add one with arg naming them")
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
            "outputs": [pfile] + [b.get("file", "HILLS") for b in metad if mode == "run"]
                       + [b.get("file", "DELTAFS" if str(b["type"]).upper() == "OPES_EXPANDED"
                                else "KERNELS")
                          for b in bias if str(b.get("type", "")).upper().startswith("OPES")],
            "warnings": warnings}
    if mode == "reweight":
        info["driver"] = ("plumed driver --plumed <this file> --ixyz traj.xyz --length-units A "
                          "(with the run's HILLS in the folder; "
                          "then yagura colvar_fes with bias ['" + metad[0]["label"] + ".bias'])")
    return "\n".join(lines) + "\n", info
