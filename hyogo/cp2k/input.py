"""Write a CP2K run input (sp, geo_opt, cell_opt, md_nvt) around subsys.inc.

The DFT block of the four templates in codes/cp2k/*/templates/ (revPBE-D3,
OT DIIS + FULL_ALL, the settings benchmarked on HOKUSAI) plus the functional
presets of the UiO-66 functional comparison: PBE,
r2SCAN, PBE0, HSE06 and r2SCAN0 with D3(BJ) and ADMM. Facts come in as
arguments: the structure's widths, charge and electrons from
cp2k_make_subsys, the data file names from rookery's cp2k_kinds.
"""
import math
import re

RUN_TYPES = {  # run_type: (RUN_TYPE, default project, walltime margin in s, template's file name)
    "sp": ("ENERGY_FORCE", "sp", 600, "single_point.inp"),
    "geo_opt": ("GEO_OPT", "opt", 600, "geo_opt.inp"),
    "cell_opt": ("CELL_OPT", "cellopt", 600, "cell_opt.inp"),
    "md_nvt": ("MD", "md", 900, "aimd_nvt.inp"),
}
DEFAULT_DURATION = {"geo_opt": "12:00:00", "cell_opt": "12:00:00", "md_nvt": "24:00:00"}
# Stress (and its Pulay error) needs a finer grid than the energy does, so a
# cell_opt below this CUTOFF gets a warning. The user's production PBE, HSE06
# and r2SCAN cell optimisations of UiO-66 run at CUTOFF 1000 / REL_CUTOFF 60 Ry.
CELL_OPT_STRESS_CUTOFF = 800

# &XC_FUNCTIONAL body per preset; ${HFX_RADIUS} is filled by @SET
_XC = {
    "revpbe": "&PBE\n  PARAMETRIZATION REVPBE\n&END PBE",
    "pbe": "&PBE\n&END PBE",
    "r2scan": "&MGGA_X_R2SCAN\n&END MGGA_X_R2SCAN\n&MGGA_C_R2SCAN\n&END MGGA_C_R2SCAN",
    "pbe0": ("&PBE\n  SCALE_X 0.75\n  SCALE_C 1.0\n&END PBE\n"
             "&PBE_HOLE_T_C_LR\n  CUTOFF_RADIUS ${HFX_RADIUS}\n  SCALE_X       0.25\n&END PBE_HOLE_T_C_LR"),
    "hse06": ("&XWPBE\n  SCALE_X  -0.25\n  SCALE_X0  1.0\n  OMEGA     0.11\n&END XWPBE\n"
              "&PBE\n  SCALE_X 0.0\n  SCALE_C 1.0\n&END PBE"),
    "r2scan0": ("&MGGA_X_R2SCAN\n  SCALE 0.75\n&END MGGA_X_R2SCAN\n"
                "&MGGA_C_R2SCAN\n&END MGGA_C_R2SCAN"),
}
# preset: potential stem, D3 reference, default damping, BJ only, default cutoff, HF interaction
PRESETS = {
    "revpbe": ("GTH-PBE", "revPBE", "zero", False, 450, None),
    "pbe": ("GTH-PBE", "PBE", "bj", False, 450, None),
    "r2scan": ("GTH-SCAN", "R2SCAN", "bj", True, 600, None),
    "pbe0": ("GTH-PBE0", "PBE0", "bj", False, 600, "truncated"),
    "hse06": ("GTH-PBE0", "HSE06", "bj", False, 600, "shortrange"),
    "r2scan0": ("GTH-SCAN", "R2SCAN0", "bj", True, 600, "truncated"),
}
FAMILY_FOR_STEM = {"GTH-PBE": '"molopt" or "uzh-pbe"', "GTH-SCAN": '"uzh-scan"',
                   "GTH-PBE0": '"uzh-pbe0"'}
MOLOPT_FILES = {"basis": ["BASIS_MOLOPT"], "potential": "GTH_POTENTIALS"}
ADMM_FILE = "BASIS_ADMM_UZH"
MD_DEFAULTS = {"temperature": 300, "timestep": 0.5, "steps": 2000, "timecon": 100}
OPT_DEFAULTS = {"max_iter": 200, "max_force": 4.5e-4, "rms_force": None,
                "keep_angles": False, "keep_symmetry": False}
HFX_DEFAULTS = {"radius": None, "memory_mb": 3000, "eps_schwarz": 1.0e-6,
                "screen_on_initial_p": None}
PRECISION_DEFAULTS = {"eps_default": 1.0e-12, "eps_pgf_orb": None}
HSE_OMEGA = 0.11  # bohr^-1, in both &XWPBE and &HF
BOHR = 0.529177210903  # angstrom


def _fmt(v) -> str:
    return f"{v:g}" if isinstance(v, float) else str(v)


def _indent(text: str, n: int) -> list[str]:
    return [" " * n + l for l in text.splitlines()]


def _seconds(hms: str) -> int:
    m = re.fullmatch(r"(\d+):([0-5]\d):([0-5]\d)", hms or "")
    if not m:
        raise ValueError(f'duration is "HH:MM:SS" (launch_plan\'s suggested_duration), not {hms!r}')
    h, mi, s = map(int, m.groups())
    return h * 3600 + mi * 60 + s


def _merge(name: str, given: dict | None, defaults: dict) -> dict:
    given = given or {}
    unknown = set(given) - set(defaults)
    if unknown:
        raise ValueError(f"{name} takes {sorted(defaults)}, not {sorted(unknown)}")
    return {**defaults, **given}


def erfc_range(eps: float, omega: float = HSE_OMEGA) -> float:
    """Distance in A where erfc(omega r)/r falls to eps: CP2K's default
    CUTOFF_RADIUS for the short-range operator."""
    lo, hi = 0.1, 500.0  # bohr; erfc(w r)/r decreases monotonically
    for _ in range(200):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if math.erfc(omega * mid) / mid > eps else (lo, mid)
    return lo * BOHR


def _hf(interaction: str, screen_p: bool) -> list[str]:
    pot = (["POTENTIAL_TYPE TRUNCATED", "CUTOFF_RADIUS  ${HFX_RADIUS}", "T_C_G_DATA     t_c_g.dat"]
           if interaction == "truncated" else
           ["POTENTIAL_TYPE SHORTRANGE", f"OMEGA          {HSE_OMEGA}", "CUTOFF_RADIUS  ${HFX_RADIUS}"])
    # EPS_SCHWARZ_FORCES defaults to 100x EPS_SCHWARZ; CP2K says it should be similar to EPS_SCF
    return (["&HF", "  FRACTION 0.25", "  &SCREENING", "    EPS_SCHWARZ          ${HFX_EPS_SCHWARZ}",
             "    EPS_SCHWARZ_FORCES   ${HFX_EPS_SCHWARZ}",
             f"    SCREEN_ON_INITIAL_P  {'TRUE' if screen_p else 'FALSE'}", "  &END SCREENING",
             "  &INTERACTION_POTENTIAL"]
            + ["    " + p for p in pot]
            + ["  &END INTERACTION_POTENTIAL", "  &MEMORY", "    MAX_MEMORY          ${HFX_MEMORY}",
               "    EPS_STORAGE_SCALING 0.1", "  &END MEMORY", "  &HF_INFO ON", "  &END HF_INFO",
               "&END HF"])


def _sci(v: float) -> str:
    return f"{v:.1E}".replace("E-0", "E-")


def _motion(run_type: str, o: dict | None = None) -> list[str]:
    if run_type in ("geo_opt", "cell_opt"):
        tol = [f"MAX_FORCE {_sci(o['max_force'])}"]
        if o["rms_force"] is not None:
            tol.append(f"RMS_FORCE {_sci(o['rms_force'])}")
    if run_type == "geo_opt":
        body = ["&GEO_OPT", "  OPTIMIZER BFGS", "  MAX_ITER  ${MAX_ITER}"] + ["  " + t for t in tol]
        body.append("&END GEO_OPT")
        prints = [("TRAJECTORY", "GEO_OPT 1"), ("RESTART", "GEO_OPT 1")]
    elif run_type == "cell_opt":
        body = ["&CELL_OPT", "  OPTIMIZER          BFGS", "  MAX_ITER           ${MAX_ITER}"]
        body += ["  " + t.replace(" ", " " * (19 - t.index(" ")), 1) for t in tol]
        body += ["  EXTERNAL_PRESSURE  [bar] 1.0", "  PRESSURE_TOLERANCE [bar] 100.0"]
        body += ["  KEEP_ANGLES        TRUE"] if o["keep_angles"] else []
        body += ["  KEEP_SYMMETRY      TRUE"] if o["keep_symmetry"] else []
        body.append("&END CELL_OPT")
        prints = [("TRAJECTORY", "CELL_OPT 1"), ("CELL", "CELL_OPT 1"), ("RESTART", "CELL_OPT 1")]
    else:
        body = ["&MD", "  ENSEMBLE    NVT", "  STEPS       ${STEPS}", "  TIMESTEP    ${TIMESTEP}",
                "  TEMPERATURE ${TEMPERATURE}", "  &THERMOSTAT", "    TYPE CSVR", "    &CSVR",
                "      TIMECON ${TIMECON}", "    &END CSVR", "  &END THERMOSTAT", "&END MD"]
        prints = [("TRAJECTORY", "MD 1"), ("VELOCITIES", "MD 1"), ("RESTART", "MD 10")]
    out = ["&MOTION"] + ["  " + l for l in body] + ["  &PRINT"]
    for section, each in prints:
        out += [f"    &{section}"] + (["      BACKUP_COPIES 1"] if section == "RESTART" else [])
        out += ["      &EACH", f"        {each}", "      &END EACH", f"    &END {section}"]
    return out + ["  &END PRINT", "&END MOTION"]


def _base(name: str) -> str:
    return name.rstrip("/").rsplit("/", 1)[-1]


def _guess_from(prev: dict, guess_dir: str | None, guess_wfn: str | None, subsys: dict,
                files: dict, warnings: list) -> tuple[str, dict]:
    """G27: the previous run's wavefunction as guess_wfn, refused unless it fits.

    A wavefunction is coefficients over one basis on one set of atoms: it can
    carry over a change of geometry or cell, or of functional, but not of
    atoms, electrons or basis. CP2K's output names only the first basis file,
    so the basis check is on that file and the potential file."""
    if guess_wfn is not None:
        raise ValueError("pass guess_wfn or guess_from, not both")
    pf = prev.get("files") if isinstance(prev, dict) else None
    if not isinstance(pf, dict) or not pf.get("wfn") or "natoms" not in prev:
        raise ValueError("guess_from is the previous run's cp2k_parse_output result (unchanged); "
                         "fs_download its .out and parse it first")
    d = (guess_dir or ".").rstrip("/") or "."
    if not re.fullmatch(r"[\w./~-]+", d):
        raise ValueError(f"guess_dir is the previous run's folder relative to this one "
                         f"(e.g. \"../hse06-cellopt\"), without spaces: {guess_dir!r}")
    checked = []
    if prev.get("natoms") is not None:
        if prev["natoms"] != subsys.get("natoms"):
            raise ValueError(f"the previous run has {prev['natoms']} atoms, this subsys "
                             f"{subsys.get('natoms')}: its wavefunction can't be reused")
        checked.append("natoms")
    if prev.get("electrons") is not None:
        if prev["electrons"] != subsys["electrons"]:
            raise ValueError(f"the previous run has {prev['electrons']} electrons, this subsys "
                             f"{subsys['electrons']} (charge or potentials differ): its "
                             "wavefunction can't be reused")
        checked.append("electrons")
    if pf.get("basis"):
        if _base(pf["basis"]) not in {_base(b) for b in files["basis"]}:
            raise ValueError(f"the previous run used basis file {pf['basis']}, this input "
                             f"{files['basis']}: a wavefunction can't move between bases; "
                             "use the same cp2k_kinds family, or a guess run (purpose=\"guess\")")
        checked.append("basis")
    if pf.get("potential"):
        if _base(pf["potential"]) != _base(files["potential"]):
            raise ValueError(f"the previous run used potential file {pf['potential']}, this input "
                             f"{files['potential']}: the wavefunction belongs to other potentials")
        checked.append("potential")
    if not prev.get("finished"):
        warnings.append("guess_from: the previous run did not finish; its wavefunction is from "
                        "its last written step")
    if (prev.get("scf") or {}).get("not_converged"):
        warnings.append("guess_from: the previous run had unconverged SCF steps; its "
                        "wavefunction may be a poor start")
    opt = prev.get("opt")
    if opt and not opt.get("converged"):
        warnings.append("guess_from: the previous optimisation did not converge; its final "
                        "structure and wavefunction are not a minimum")
    wfn = pf["wfn"] if d == "." else f"{d}/{pf['wfn']}"
    return wfn, {"wfn": wfn, "from_project": prev.get("project"), "checked": checked,
                 "final_xyz": pf.get("final_xyz")}


def make_input(run_type: str, functional: str, subsys: dict, files: dict | None = None,
               project: str | None = None, cutoff: int | None = None, rel_cutoff: int = 60,
               d3: str | None = None, scf: str = "ot", multiplicity: int | None = None,
               duration: str | None = None, md: dict | None = None, opt: dict | None = None,
               hfx: dict | None = None, print_gap: bool = False,
               save_wfn: bool = False, stress: bool = False,
               precision: dict | None = None, guess_wfn: str | None = None,
               purpose: str = "result", guess_from: dict | None = None,
               guess_dir: str | None = None) -> tuple[str, dict]:
    """The input text and a summary; `subsys` is cp2k_make_subsys' result.

    purpose="guess" writes a GGA single point whose only product is the
    wavefunction a hybrid restarts from, in the hybrid's basis and
    potentials (a wavefunction can't move between bases). Its energy is not
    a result: the potentials belong to another functional."""
    if purpose not in ("result", "guess"):
        raise ValueError('purpose is "result" or "guess" (a wavefunction for a hybrid to restart from)')
    if run_type not in RUN_TYPES:
        raise ValueError(f"run_type is one of {list(RUN_TYPES)}")
    if functional not in PRESETS:
        raise ValueError(f"functional is one of {list(PRESETS)}")
    if scf not in ("ot", "smear"):
        raise ValueError('scf is "ot" (insulators) or "smear" (metals, small gaps)')
    need = ("periodic", "electrons", "widths", "kinds")
    if not isinstance(subsys, dict) or any(k not in subsys for k in need):
        raise ValueError(f"subsys is cp2k_make_subsys' result (needs {list(need)})")
    stem, d3_ref, d3_default, bj_only, cutoff_default, hf = PRESETS[functional]
    warnings = []
    guess_info = None
    if guess_dir is not None and guess_from is None:
        raise ValueError("guess_dir goes with guess_from (the previous run's cp2k_parse_output)")

    if purpose == "guess":
        if hf:
            raise ValueError(f"purpose=\"guess\" is a GGA run that seeds a hybrid; {functional} is "
                             "the hybrid itself: use pbe (or revpbe)")
        if run_type != "sp" or stress:
            raise ValueError('purpose="guess" is a single point (run_type="sp") without stress')
        if files is None:
            raise ValueError("purpose=\"guess\" needs cp2k_kinds' result for the hybrid's data files")
        save_wfn = True
        warnings.append("guess only: this energy is NOT a result (the potentials belong to the "
                        "hybrid); use only its wavefunction, as the hybrid's guess_wfn")
    wrong = {e: p for e, p in subsys["kinds"].items() if not re.fullmatch(re.escape(stem) + r"-q\d+", p)}
    if wrong and purpose != "guess":
        raise ValueError(f"{functional} needs {stem} potentials, subsys.inc has {wrong}: call "
                         f"cp2k_kinds(family={FAMILY_FOR_STEM[stem]}) and cp2k_make_subsys again")
    if files is None:
        if stem != "GTH-PBE":
            raise ValueError("pass cp2k_kinds' result (its `files` name the UZH data files)")
        files = MOLOPT_FILES
        warnings.append("no cp2k_kinds result given: assumed BASIS_MOLOPT and GTH_POTENTIALS; "
                        "they must be the files subsys.inc's kinds came from")
    files = files.get("files", files)
    if not (isinstance(files, dict) and isinstance(files.get("basis"), list) and files.get("potential")):
        raise ValueError("files is cp2k_kinds' result or its `files` ({basis: [...], potential})")
    if hf:
        missing = [e for e in subsys["kinds"] if e not in (subsys.get("aux_basis") or {})]
        if missing or ADMM_FILE not in files["basis"]:
            raise ValueError(f"{functional} uses ADMM: call cp2k_kinds(..., aux_basis=\"admm-dzp\") "
                             f"and cp2k_make_subsys again (no AUX_FIT basis for {missing or 'the files'})")
    if guess_from is not None:
        guess_wfn, guess_info = _guess_from(guess_from, guess_dir, guess_wfn, subsys, files, warnings)

    d3 = d3 or d3_default
    if d3 not in ("zero", "bj"):
        raise ValueError('d3 is "zero" or "bj"')
    if d3 == "zero" and bj_only:
        raise ValueError(f"{functional} has D3 parameters only with BJ damping: use d3=\"bj\"")

    electrons = subsys["electrons"]
    if multiplicity is None:
        multiplicity = 2 if electrons % 2 else 1
    if multiplicity < 1 or (electrons + multiplicity) % 2 == 0:
        raise ValueError(f"multiplicity {multiplicity} is impossible with {electrons} electrons")
    if multiplicity > 1:
        warnings.append(f"open shell: UKS with multiplicity {multiplicity}")

    cutoff = cutoff or (max(cutoff_default, 600) if run_type == "cell_opt" else cutoff_default)
    project = project or ("guess" if purpose == "guess" else RUN_TYPES[run_type][1])
    sets = {"PROJECT": project, "CHARGE": subsys.get("charge", 0),
            "MULTIPLICITY": multiplicity, "UKS": ".TRUE." if multiplicity > 1 else ".FALSE.",
            "CUTOFF": cutoff, "REL_CUTOFF": rel_cutoff}

    if hf:
        h = _merge("hfx", hfx, HFX_DEFAULTS)
        if not 0 < h["eps_schwarz"] < 1e-3:
            raise ValueError("hfx eps_schwarz is a small threshold, e.g. 1.0E-6")
        # Gamma-point HFX: an operator reaching past half the cell counts periodic images twice
        half = min(subsys["widths"]) / 2
        below_half = math.floor((half - 1e-6) * 10) / 10
        if h["radius"] is not None:
            radius = h["radius"]
        elif hf == "truncated":
            radius = below_half
        else:
            reach = erfc_range(h["eps_schwarz"])
            radius = math.ceil(reach * 10) / 10
            if radius >= half:
                radius = below_half
                warnings.append(f"the short-range operator reaches {reach:.1f} A at EPS_SCHWARZ "
                                f"{h['eps_schwarz']:g}, past half the cell ({half:.2f} A): capped at "
                                f"{radius} A. Converge against it, or use a supercell")
        if radius >= half:
            raise ValueError(f"HFX radius {radius} A must stay below half the narrowest cell "
                             f"width ({half:.2f} A); use a supercell for a larger radius")
        sets["HFX_RADIUS"] = radius
        sets["HFX_EPS_SCHWARZ"] = f"{h['eps_schwarz']:.1E}".replace("E-0", "E-")
        if hf == "truncated":
            warnings.append(f"truncated exact exchange at {radius} A: converge the result against "
                            "the radius, and the build's data folder must hold t_c_g.dat")
            if functional == "r2scan0":
                warnings.append("r2SCAN0 has no long-range correction: the 25 % exact exchange "
                                "beyond the radius is simply missing")
        sets["HFX_MEMORY"] = h["memory_mb"]
        screen_p = h["screen_on_initial_p"]
        if screen_p is None:
            screen_p = guess_wfn is not None  # safe from a converged restart, not an atomic guess
        if guess_wfn is None:
            warnings.append("hybrid from the atomic guess: SCF steps cost the full HFX price; "
                            "start from a GGA wavefunction in the same basis (guess_wfn)")
        warnings.append(f"hybrid with ADMM: expect ~5-20x the GGA cost per step and "
                        f"{h['memory_mb']} MB HFX memory per MPI rank; run a short test job first")
    elif hfx:
        raise ValueError(f"{functional} has no exact exchange; drop hfx")

    o = None
    if run_type in ("geo_opt", "cell_opt"):
        o = _merge("opt", opt, OPT_DEFAULTS)
        sets["MAX_ITER"] = o["max_iter"]
        if not 0 < o["max_force"] < 1e-1:
            raise ValueError("opt max_force is in Ha/bohr, e.g. 4.5E-4 (CP2K's default) or 1.0E-4")
        if o["rms_force"] is not None and not 0 < o["rms_force"] <= o["max_force"]:
            raise ValueError("opt rms_force is positive and at most max_force")
        if run_type == "geo_opt" and (o["keep_angles"] or o["keep_symmetry"]):
            raise ValueError("keep_angles and keep_symmetry are for cell_opt; geo_opt keeps the cell fixed")
        if o["keep_symmetry"] and not subsys.get("symmetry"):
            raise ValueError("keep_symmetry needs &CELL SYMMETRY: call cp2k_make_subsys with "
                             "symmetry=... (e.g. \"CUBIC\") and pass its result")
    elif opt:
        raise ValueError("opt is for geo_opt and cell_opt")
    if run_type != "sp":
        # continuing after a walltime stop: set RESTART 1 and resubmit
        sets["RESTART"] = 0
        if guess_wfn is None:
            sets["SCF_GUESS"] = "ATOMIC"
        else:
            sets["WFN"] = guess_wfn
    if run_type == "md_nvt":
        m = _merge("md", md, MD_DEFAULTS)
        sets.update(TEMPERATURE=m["temperature"], TIMESTEP=m["timestep"], STEPS=m["steps"],
                    TIMECON=m["timecon"])
        if "H" in subsys["kinds"] and m["timestep"] > 0.5:
            warnings.append(f"timestep {m['timestep']} fs with hydrogen present: 0.5 fs is safe")
    elif md:
        raise ValueError("md is for md_nvt")
    if duration is None and run_type in DEFAULT_DURATION:
        duration = DEFAULT_DURATION[run_type]
        warnings.append(f"WALLTIME assumes a {duration} job; pass launch_plan's suggested_duration")
    if duration is not None:
        margin = RUN_TYPES[run_type][2]
        if _seconds(duration) <= margin:
            raise ValueError(f"duration {duration} leaves no time after the {margin} s margin")
        sets["WALLTIME"] = _seconds(duration) - margin
    if run_type == "cell_opt" and cutoff < CELL_OPT_STRESS_CUTOFF:
        warnings.append(f"CUTOFF {cutoff} Ry is below {CELL_OPT_STRESS_CUTOFF} Ry: stress converges "
                        "more slowly with CUTOFF than the energy (Pulay stress), so converge the "
                        f"stress against CUTOFF or use >= {CELL_OPT_STRESS_CUTOFF} Ry before "
                        "trusting the cell")
    if stress and run_type != "sp":
        raise ValueError("stress is for sp; cell_opt computes it anyway")
    prec = _merge("precision", precision, PRECISION_DEFAULTS)
    if guess_wfn is not None and not re.fullmatch(r"[\w./~-]+", guess_wfn):
        raise ValueError(f"guess_wfn is a path on the cluster, without spaces: {guess_wfn!r}")
    if scf == "smear" and run_type == "md_nvt":
        warnings.append("smearing in MD: the conserved quantity drifts more; watch it")

    width = max(len(k) for k in sets) + 2
    lines = [f"# CP2K {run_type}, {functional}-D3{'(BJ)' if d3 == 'bj' else ''}, "
             f"{subsys.get('formula', '?')}; written by cp2k_make_input",
             "# Edit the @SET values; the structure is in subsys.inc."]
    if purpose == "guess":
        lines.append("# GUESS ONLY: the energy is not a result; the wavefunction seeds a hybrid.")
    if "RESTART" in sets:
        lines.append("# RESTART 1 continues from ${PROJECT}-1.restart and its wavefunction "
                     "(after a walltime stop).")
    lines += [f"@SET {k:<{width}}{_fmt(v)}" for k, v in sets.items()]
    if "RESTART" in sets:
        lines += ["@IF ${RESTART} == 1",
                  f"@SET {'WFN' if 'WFN' in sets else 'SCF_GUESS':<{width}}"
                  + ("${PROJECT}-RESTART.wfn" if "WFN" in sets else "RESTART"),
                  "@ENDIF"]
    lines += ["", "&GLOBAL", "  PROJECT     ${PROJECT}", f"  RUN_TYPE    {'ENERGY' if purpose == 'guess' else RUN_TYPES[run_type][0]}",
              "  PRINT_LEVEL LOW"]
    if "WALLTIME" in sets:
        lines.append("  WALLTIME    ${WALLTIME}")
    lines += ["&END GLOBAL", ""]
    if "RESTART" in sets:
        lines += ["@IF ${RESTART} == 1", "&EXT_RESTART", "  RESTART_FILE_NAME ${PROJECT}-1.restart",
                  "&END EXT_RESTART", "@ENDIF", ""]
    if run_type != "sp":
        lines += _motion(run_type, o) + [""]

    lines += ["&FORCE_EVAL", "  METHOD QS"]
    if run_type == "cell_opt":
        lines += ["  STRESS_TENSOR ANALYTICAL", "  &PRINT", "    &STRESS_TENSOR ON",
                  "    &END STRESS_TENSOR", "  &END PRINT"]
    elif run_type == "sp":
        lines += ["  STRESS_TENSOR ANALYTICAL"] if stress else []
        lines += ["  &PRINT", "    &FORCES ON", "    &END FORCES"]
        lines += ["    &STRESS_TENSOR ON", "      STRESS_UNIT GPa", "    &END STRESS_TENSOR"] if stress else []
        lines.append("  &END PRINT")
    lines.append("  &DFT")
    lines += [f"    BASIS_SET_FILE_NAME {f}" for f in files["basis"]]
    lines.append(f"    POTENTIAL_FILE_NAME {files['potential']}")
    if guess_wfn:
        lines.append(f"    WFN_RESTART_FILE_NAME {'${WFN}' if 'WFN' in sets else guess_wfn}")
    if hf:
        lines += ["    &AUXILIARY_DENSITY_MATRIX_METHOD", "      METHOD                   BASIS_PROJECTION",
                  "      ADMM_PURIFICATION_METHOD NONE",
                  # DEFAULT is the PBE-based correction for the HF operator in use (short-range
                  # wPBE for HSE06, truncated PBE hole for PBE0); PBEX is full-range only
                  "      EXCH_CORRECTION_FUNC     DEFAULT",
                  "    &END AUXILIARY_DENSITY_MATRIX_METHOD"]
    lines += ["    CHARGE       ${CHARGE}", "    MULTIPLICITY ${MULTIPLICITY}", "    UKS          ${UKS}",
              "    &MGRID", "      CUTOFF     ${CUTOFF}", "      REL_CUTOFF ${REL_CUTOFF}", "    &END MGRID",
              "    &QS", f"      EPS_DEFAULT {prec['eps_default']:.1E}"]
    if prec["eps_pgf_orb"] is not None:
        lines.append(f"      EPS_PGF_ORB {prec['eps_pgf_orb']:.1E}")
    if hf:
        # otherwise HFX terms outside the orbital pair list are dropped (CP2K warns:
        # "The Kohn Sham matrix is not 100% occupied")
        lines.append("      MIN_PAIR_LIST_RADIUS -1")
    if run_type != "sp":
        lines += ["      EXTRAPOLATION       ASPC", "      EXTRAPOLATION_ORDER 3"]
    lines.append("    &END QS")
    if subsys["periodic"] == "NONE":
        lines += ["    &POISSON", "      PERIODIC NONE", "      PSOLVER  MT", "    &END POISSON"]
    # No IGNORE_CONVERGENCE_FAILURE: an unconverged SCF must stop the run.
    guess = "${SCF_GUESS}" if "SCF_GUESS" in sets else "RESTART" if guess_wfn else "ATOMIC"
    lines += ["    &SCF", f"      SCF_GUESS {guess}", "      EPS_SCF   1.0E-6",
              # hybrids and MD: inner MAX_SCF 30 under OUTER_SCF (CP2K docs, convergence page: ~16-32)
              f"      MAX_SCF   {30 if run_type == 'md_nvt' or hf else 50}"]
    if scf == "ot":
        lines += ["      &OT ON", "        MINIMIZER      DIIS", "        PRECONDITIONER FULL_ALL",
                  "        ENERGY_GAP     0.05", "      &END OT", "      &OUTER_SCF", "        EPS_SCF 1.0E-6",
                  "        MAX_SCF 10", "      &END OUTER_SCF"]
    else:
        lines += ["      ADDED_MOS 100", "      &DIAGONALIZATION", "        ALGORITHM STANDARD",
                  "      &END DIAGONALIZATION", "      &SMEAR ON", "        METHOD FERMI_DIRAC",
                  "        ELECTRONIC_TEMPERATURE [K] 300", "      &END SMEAR", "      &MIXING",
                  "        METHOD BROYDEN_MIXING", "        ALPHA 0.2", "      &END MIXING"]
    if run_type == "md_nvt":
        lines += ["      &PRINT", "        &RESTART", "          BACKUP_COPIES 0", "          &EACH",
                  "            MD 10", "          &END EACH", "        &END RESTART", "      &END PRINT"]
    elif save_wfn:
        lines += ["      &PRINT", "        &RESTART ON", "        &END RESTART", "      &END PRINT"]
    lines.append("    &END SCF")
    if print_gap:
        lines += ["    &PRINT", "      &MO_CUBES", "        NHOMO 1", "        NLUMO 1",
                  "        WRITE_CUBE FALSE", "      &END MO_CUBES", "    &END PRINT"]
    lines += ["    &XC", "      &XC_FUNCTIONAL"] + _indent(_XC[functional], 8) + ["      &END XC_FUNCTIONAL"]
    if hf:
        lines += _indent("\n".join(_hf(hf, screen_p)), 6)
    lines += ["      &VDW_POTENTIAL", "        POTENTIAL_TYPE PAIR_POTENTIAL", "        &PAIR_POTENTIAL",
              f"          TYPE                 {'DFTD3(BJ)' if d3 == 'bj' else 'DFTD3'}",
              "          PARAMETER_FILE_NAME  dftd3.dat", f"          REFERENCE_FUNCTIONAL {d3_ref}",
              "        &END PAIR_POTENTIAL", "      &END VDW_POTENTIAL", "    &END XC", "  &END DFT",
              "  &SUBSYS", "    @INCLUDE subsys.inc", "  &END SUBSYS", "&END FORCE_EVAL"]

    info = {"run_type": run_type, "functional": functional, "d3": d3, "scf": scf, "purpose": purpose,
            "wfn": f"{project}-RESTART.wfn" if save_wfn and run_type == "sp" else None,
            "restart": ({"set": "RESTART", "file": f"{project}-1.restart",
                         "wfn": f"{project}-RESTART.wfn"} if "RESTART" in sets else None),
            "settings": {k: v for k, v in sets.items() if k != "PROJECT"},
            "opt": o, "guess": guess_info,
            "files": files["basis"] + [files["potential"]] + (["t_c_g.dat"] if hf == "truncated" else []),
            "warnings": warnings}
    return "\n".join(lines) + "\n", info
