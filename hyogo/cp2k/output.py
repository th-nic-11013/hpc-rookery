"""Read a CP2K output (.out) into run-control facts, printed results and
cost facts (G15).

Text in, dict out: no file writes, no cluster. It reads only what CP2K
prints; it computes nothing a physics analysis would (that is yagura's).
The force and timer patterns are a deliberate copy of rookery's
hpc_codes/cp2k/parse_bench.py (hyogo imports nothing from rookery); the
conformance test checks the two agree on the same files.
"""
import re

NUM = r"[-+]?\d+(?:\.\d*)?(?:[EDed][-+]?\d+)?"

# copied from hpc_codes/cp2k/parse_bench.py: 2026.x "FORCES|" table and the older one
FORCES_TAG = re.compile(r"^ FORCES\|\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s*$")
FORCE_OLD = re.compile(rf"^\s+(\d+)\s+\d+\s+[A-Z][a-z]?\s+({NUM})\s+({NUM})\s+({NUM})\s*$")
TIMER = re.compile(r"^ ([A-Za-z_]\w*)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")

OUTER_OK = re.compile(r"outer SCF loop converged in\s+(\d+) iterations or\s+(\d+) steps")
OUTER_FAIL = re.compile(r"outer SCF loop FAILED to converge after\s+(\d+) iterations or\s+(\d+) steps")
INNER_OK = re.compile(r"\*\*\* SCF run converged in\s+(\d+) steps \*\*\*")
INNER_FAIL = re.compile(r"\*\*\* SCF run NOT converged \*\*\*")
CELL_LEN = re.compile(rf"^ CELL\| Vector ([abc]) \[angstrom\]:.*\|[abc]\| =\s+({NUM})")
CELL_ANG = re.compile(rf"^ CELL\| Angle \([abc],[abc]\), (alpha|beta|gamma)\s+\[degree\]:\s+({NUM})")
CELL_VOL = re.compile(rf"^ CELL\| Volume \[angstrom\^3\]:\s+({NUM})")
STRESS_ROW = re.compile(rf"^ STRESS\|\s+([xyz])\s+({NUM})\s+({NUM})\s+({NUM})\s*$")
PEAK = re.compile(r"Estimated peak process memory.*\[MiB\]\s+(\d+)")
WARN = re.compile(r"^ \*\*\* WARNING in (\S+) :: (.*?)\s*\*\*\*\s*$")
BOX = re.compile(r"^ \*\*\* (.*?)\s*\*\*\*\s*$")   # continuation line of a warning box
# OPT| label -> key; CP2K prints "<label>", "Convergence limit for <label, lower case>",
# "<label> is converged  YES|NO"
CRITERIA = {"Maximum step size": "max_step", "RMS step size": "rms_step",
            "Maximum gradient": "max_gradient", "RMS gradient": "rms_gradient"}
LIMIT_LABEL = {"Maximum step size": "maximum step size", "RMS step size": "RMS step size",
               "Maximum gradient": "maximum gradient", "RMS gradient": "RMS gradient"}
TOP_TIMERS = 8


def _f(s: str) -> float:
    return float(s.replace("D", "E").replace("d", "e"))


def _tail(line: str) -> str:
    return line.split()[-1]


def parse(text: str, vectors: bool = False) -> dict:
    """Parse the text of a CP2K output. Raises ValueError if it isn't one.

    vectors=True adds forces["vectors"]: the last force printout, one
    [x, y, z] per atom in atom order (Ha/bohr), as CP2K printed it."""
    lines = text.splitlines()
    if not any(l.startswith((" CP2K| version", " GLOBAL| Run type")) for l in lines[:200]):
        raise ValueError("not a CP2K output: no CP2K| or GLOBAL| header in the first 200 lines")

    r = {"version": None, "project": None, "run_type": None, "finished": False,
         "started": None, "ranks": None, "threads": None, "natoms": None, "electrons": None}
    scf_steps, scf_outer, scf_nc, inner_pending = [], [], 0, []
    energies, gaps, disp = [], [], []
    forces, cur_forces, in_old_forces = None, [], False
    stress, cur_stress, third_trace = None, {}, None
    cell, cur_cell = None, {}
    opt_steps, opt_crit, opt_press, opt_e, opt_done, opt_maxiter = 0, {}, None, None, False, False
    md_steps, md_times, opt_times = 0, [], []
    peak, timers, in_timing = 0, {}, False
    hfx = {}
    warns, warn_open = {}, None
    # G27: file names the next run needs (relative to the run folder)
    files = {"final_xyz": None, "final_cif": None, "basis": None, "potential": None}
    final_next = None   # "final_xyz" / "final_cif" when the next line holds the name

    for line in lines:
        s = line.strip()
        # --- header facts
        if line.startswith(" CP2K| version string:"):
            r["version"] = line.split(":", 1)[1].strip()
        elif line.startswith(" GLOBAL| Project name"):
            r["project"] = _tail(line)
        elif line.startswith(" GLOBAL| Run type"):
            r["run_type"] = _tail(line)
        elif line.startswith(" GLOBAL| Total number of message passing processes"):
            r["ranks"] = int(_tail(line))
        elif line.startswith(" GLOBAL| Number of threads for this process"):
            r["threads"] = int(_tail(line))
        elif r["started"] is None and "PROGRAM STARTED AT" in line:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", line)
            r["started"] = m.group(1) if m else None
        elif r["natoms"] is None and re.match(r"^\s+- Atoms:\s+\d+", line):
            r["natoms"] = int(_tail(line))
        elif r["electrons"] is None and s.startswith("Number of electrons:"):
            r["electrons"] = int(_tail(line))
        elif "PROGRAM ENDED AT" in line:
            r["finished"] = True
        elif line.startswith(" GLOBAL| Basis set file name"):
            files["basis"] = _tail(line)     # CP2K prints only the first BASIS_SET_FILE_NAME
        elif line.startswith(" GLOBAL| Potential file name"):
            files["potential"] = _tail(line)

        # --- final structure files, named on the line after write_final_structure's notice
        if final_next is not None:
            if s:
                files[final_next] = s
            final_next = None
        elif line.startswith(" write_final_structure:") and "writing XYZ file" in line:
            final_next = "final_xyz"
        elif line.startswith(" write_final_structure:") and "writing CIF file" in line:
            final_next = "final_cif"

        # --- SCF: one evaluation ends at an outer-loop line, or (no outer loop) at the inner one
        m = INNER_OK.search(line)
        if m:
            inner_pending.append(int(m.group(1)))
        elif INNER_FAIL.search(line):
            inner_pending.append(None)
        m = OUTER_OK.search(line) or OUTER_FAIL.search(line)
        if m:
            scf_outer.append(int(m.group(1)))
            scf_steps.append(int(m.group(2)))
            if "FAILED" in line:
                scf_nc += 1
            inner_pending = []
        if line.startswith(" ENERGY| Total FORCE_EVAL"):
            energies.append(_f(_tail(line)))
            if inner_pending:            # no outer loop printed for this evaluation
                scf_outer.append(0)
                scf_steps.append(sum(x or 0 for x in inner_pending))
                scf_nc += any(x is None for x in inner_pending)
                inner_pending = []
        if "HOMO - LUMO gap [eV]" in line:
            gaps.append(_f(_tail(line)))
        if s.startswith("Dispersion energy:"):      # G39; printed in each SCF summary
            disp.append(_f(_tail(line)))

        # --- forces (last printout wins)
        if line.startswith(" FORCES|"):
            m = FORCES_TAG.match(line)
            if m:
                cur_forces.append((int(m.group(1)), *(_f(x) for x in m.groups()[1:])))
            elif " forces [" in line:
                cur_forces = []
            continue
        if "ATOMIC FORCES in [a.u.]" in line:
            in_old_forces, cur_forces = True, []
            continue
        if in_old_forces:
            m = FORCE_OLD.match(line)
            if m:
                cur_forces.append((int(m.group(1)), *(_f(x) for x in m.groups()[1:])))
                continue
            if "SUM OF ATOMIC FORCES" in line:
                in_old_forces = False
                forces = list(cur_forces)
                continue
        if cur_forces and not in_old_forces and not line.startswith(" FORCES|"):
            forces, cur_forces = list(cur_forces), []

        # --- stress (analytical tensor block; the eigenvector rows share the x/y/z labels)
        if line.startswith(" STRESS| Analytical stress tensor [GPa]"):
            cur_stress = {"open": True}
        elif line.startswith(" STRESS|") and cur_stress.get("open"):
            m = STRESS_ROW.match(line)
            if m:
                cur_stress[m.group(1)] = [_f(x) for x in m.groups()[1:]]
            elif "1/3 Trace" in line:
                third_trace = _f(_tail(line))
                if all(k in cur_stress for k in "xyz"):
                    stress = [cur_stress[k] for k in "xyz"]
                cur_stress = {}

        # --- cell (last CELL| block)
        m = CELL_LEN.match(line)
        if m:
            cur_cell[m.group(1)] = _f(m.group(2))
        m = CELL_ANG.match(line)
        if m:
            cur_cell[m.group(1)] = _f(m.group(2))
        m = CELL_VOL.match(line)
        if m:
            cur_cell["volume"] = _f(m.group(1))
        if len(cur_cell) == 7:
            cell, cur_cell = dict(cur_cell), {}

        # --- optimisation
        if line.startswith(" OPT| Step number"):
            opt_steps = int(_tail(line))
        elif line.startswith(" OPT| Total energy [hartree]"):
            opt_e = _f(_tail(line))
        elif line.startswith(" OPT| Internal pressure [bar]"):
            opt_press = _f(_tail(line))
        elif line.startswith(" OPT| Used time [s]"):
            opt_times.append(_f(_tail(line)))
        elif line.startswith(" OPT| "):
            body = line[6:].strip()
            for label, key in CRITERIA.items():
                if body.startswith(label) and not body.endswith(("YES", "NO")):
                    opt_crit.setdefault(key, {})["value"] = _f(body.split()[-1])
                elif body.startswith(f"Convergence limit for {LIMIT_LABEL[label]}"):
                    opt_crit.setdefault(key, {})["limit"] = _f(body.split()[-1])
                elif body.startswith(f"{label} is converged"):
                    opt_crit.setdefault(key, {})["converged"] = body.endswith("YES")
            if body.startswith("Pressure deviation [bar]"):
                opt_crit.setdefault("pressure_dev_bar", {})["value"] = _f(body.split()[-1])
            elif body.startswith("Pressure tolerance [bar]"):
                opt_crit.setdefault("pressure_dev_bar", {})["limit"] = _f(body.split()[-1])
            elif body.startswith("Pressure is converged"):
                opt_crit.setdefault("pressure_dev_bar", {})["converged"] = body.endswith("YES")
        if "GEOMETRY OPTIMIZATION COMPLETED" in line:
            opt_done = True
        if "MAXIMUM NUMBER OF OPTIMIZATION STEPS REACHED" in line:
            opt_maxiter = True

        # --- MD
        if line.startswith(" MD| Step number"):
            md_steps = int(_tail(line))
        elif line.startswith(" MD| CPU time per MD step [s]"):
            md_times.append(_f(line.split()[-2]))

        # --- cost
        m = PEAK.search(line)
        if m:
            peak = max(peak, int(m.group(1)))
        if line.startswith("  HFX_MEM_INFO|"):
            for label, key in (("Total memory consumption ERI's RAM [MiB]", "eri_ram_mib"),
                               ("Total memory consumption ERI's disk [MiB]", "eri_disk_mib"),
                               ("Number of sph. ERI's calculated on the fly", "on_the_fly"),
                               ("Est. max. program size after HFX  [MiB]", "max_program_mib")):
                if label in line:
                    hfx[key] = int(_tail(line))  # last evaluation wins
        if "T I M I N G" in line:
            in_timing, timers = True, {}
        elif in_timing:
            m = TIMER.match(line)
            if m:
                try:
                    timers[m.group(1)] = _f(m.group(7))  # total time, max over ranks
                except ValueError:
                    pass

        # --- CP2K warnings: a box of " *** ... ***" lines, the first naming the source
        m = WARN.match(line)
        if m:
            warn_open = [f"{m.group(1)}: {m.group(2)}"]
        elif warn_open is not None:
            m = BOX.match(line)
            if m and m.group(1):
                warn_open.append(m.group(1))
            else:
                key = " ".join(warn_open)
                warns[key] = warns.get(key, 0) + 1
                warn_open = None

    if warn_open is not None:
        key = " ".join(warn_open)
        warns[key] = warns.get(key, 0) + 1
    if cur_forces and forces is None:
        forces = cur_forces
    r["scf"] = {"evaluations": len(scf_steps), "not_converged": scf_nc,
                "steps": scf_steps, "outer": scf_outer}
    r["energy_ha"] = energies[-1] if energies else None
    r["gap_ev"] = gaps[-1] if gaps else None
    r["dispersion_ha"] = disp[-1] if disp else None
    if forces:
        mags = [(a, (x * x + y * y + z * z) ** 0.5) for a, x, y, z in forces]
        atom, fmax = max(mags, key=lambda t: t[1])
        r["forces"] = {"max": fmax, "rms": (sum(m * m for _, m in mags) / len(mags)) ** 0.5,
                       "max_atom": atom, "natoms": len(mags)}
        if vectors:
            order = sorted(forces)
            if [a for a, *_ in order] != list(range(1, len(order) + 1)):
                raise ValueError("the force printout does not number the atoms 1..N once each")
            r["forces"]["vectors"] = [[x, y, z] for _, x, y, z in order]
    else:
        r["forces"] = None
    r["stress_gpa"] = {"tensor": stress, "third_trace": third_trace} if stress else None
    r["cell"] = ({k: cell[k] for k in ("a", "b", "c", "alpha", "beta", "gamma", "volume")}
                 if cell else None)

    is_opt = r["run_type"] in ("GEO_OPT", "CELL_OPT")
    r["opt"] = None
    if is_opt:
        r["opt"] = {"converged": opt_done, "steps": opt_steps, "max_iter_hit": opt_maxiter,
                    "criteria": opt_crit or None, "internal_pressure_bar": opt_press,
                    "energy_ha": opt_e,
                    # one per force evaluation, the first being step 0 (the
                    # start geometry): still there when the run is killed
                    "step_times_s": opt_times}
    r["md"] = ({"steps": md_steps,
                "s_per_step": (sum(md_times[2:] or md_times) / len(md_times[2:] or md_times))
                if md_times else None}
               if r["run_type"] == "MD" else None)
    top = sorted(timers.items(), key=lambda kv: -kv[1])
    r["cost"] = {"cp2k_s": timers.get("CP2K"), "peak_mib": peak or None,
                 "top_timers": [[k, v] for k, v in top if k != "CP2K"][:TOP_TIMERS],
                 "hfx": hfx or None}

    project = r["project"]
    files["wfn"] = f"{project}-RESTART.wfn" if project else None
    files["restart"] = (f"{project}-1.restart"
                        if project and r["run_type"] in ("GEO_OPT", "CELL_OPT", "MD") else None)
    r["files"] = files

    w = []
    if not r["finished"]:
        w.append("the output has no PROGRAM ENDED: the job is still running or was killed "
                 "(get_job_status); the values are from the last complete step")
    if scf_nc:
        w.append(f"{scf_nc} SCF evaluation(s) did not converge: energies and forces there are "
                 "not trustworthy")
    if is_opt and not opt_done:
        if opt_maxiter:
            w.append(f"the optimisation hit MAX_ITER after {opt_steps} steps without converging: "
                     "restart it (@SET RESTART 1) with a larger max_iter, or check the setup")
        elif r["finished"]:
            w.append(f"the optimisation stopped after {opt_steps} steps without converging "
                     "(most likely WALLTIME): set @SET RESTART 1 in the input and resubmit")
    for k, n in warns.items():
        w.append(f"CP2K warning ({n}x): {k}")
    r["warnings"] = w
    return r
