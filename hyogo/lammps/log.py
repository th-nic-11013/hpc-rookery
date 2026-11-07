"""Read a LAMMPS log (log.lammps) into run-control and timing facts.

Taught from HOKUSAI's MLMD benchmark parser (~/software/mlip/2026.10/bench/
parse_mlmd.py: parse_log and run_info; copied to tests/fixtures/lammps/
hokusai_bench/). That script knew each run's ensemble from its folder name;
this reads it from the log itself: LAMMPS echoes every command, so the fixes
active at each `run` label its thermo block. Layout planning and benchmark
records (parse_mlmd's plan, pick, scan-target) are rookery's.
"""
import math
import re

LOOP = re.compile(r"^Loop time of (\S+) on (\d+) procs for (\d+) steps with (\d+) atoms")
TASKS = re.compile(r"(\d+) MPI tasks x (\d+) OpenMP threads")
# timestep units -> ps
DT_PS = {"metal": 1.0, "real": 1e-3, "electron": 1e-3 * 0.0241888, "si": 1e12, "cgs": 1e12,
         "micro": 1e6, "nano": 1e3}
THERMOSTATS = {"nvt", "npt", "nph", "langevin", "temp/berendsen", "temp/rescale", "temp/csvr",
               "temp/csld", "nvt/sllod", "gld", "gle"}
INTEGRATORS = {"nve", "nvt", "npt", "nph", "nve/limit", "rigid", "rigid/nvt", "rigid/npt",
               "nvt/sllod"}


def _fit(t, e):
    """Slope and residual standard deviation of a straight-line fit (as parse_mlmd.py)."""
    tm, em = sum(t) / len(t), sum(e) / len(e)
    slope = sum((a - tm) * (b - em) for a, b in zip(t, e)) / sum((a - tm) ** 2 for a in t)
    std = math.sqrt(sum((b - em - slope * (a - tm)) ** 2 for a, b in zip(t, e)) / len(e))
    return slope, std


def parse(text: str) -> dict:
    version = None
    units, ts, fixes = "lj", None, {}
    runs, cur, header = [], None, None
    error, warns, threads, ranks, finished = None, [], None, None, False
    dangerous = 0
    for line in text.splitlines():
        if version is None and line.startswith("LAMMPS ("):
            version = line[8:].rstrip(")").strip()
        w = line.split("#", 1)[0].split()
        if w and w[0] == "Step" and len(w) > 1 and not w[1].isdigit():
            header = w
            cur = {"columns": w, "rows": [], "fixes": dict(fixes), "units": units,
                   "timestep": ts, "loop": None}
            runs.append(cur)
            continue
        m = LOOP.match(line)
        if m and cur is not None:
            cur["loop"] = dict(time_s=float(m[1]), procs=int(m[2]), steps=int(m[3]),
                               atoms=int(m[4]))
            header = None
            continue
        if header and w and len(w) == len(header):
            try:
                cur["rows"].append([float(x) for x in w])
                continue
            except ValueError:
                header = None
        elif header and w:
            header = None
        m = TASKS.search(line)
        if m:
            ranks, threads = int(m[1]), int(m[2])
        if line.startswith("ERROR"):
            error = error or line.strip()
        elif line.startswith("WARNING") and line.strip() not in warns:
            warns.append(line.strip())
        if line.startswith("Total wall time"):
            finished = True
        m = re.match(r"Dangerous builds\s*=\s*(\d+)", line)
        if m:
            dangerous += int(m[1])
        # echoed commands start at column 0; LAMMPS' own notes are indented
        if not w or line[:1].isspace() or "${" in line or "$(" in line:
            continue
        if w[0] == "units" and len(w) == 2:
            units = w[1]
        elif w[0] == "timestep" and len(w) == 2:
            try:
                ts = float(w[1])
            except ValueError:
                pass
        elif w[0] == "fix" and len(w) >= 4:
            fixes[w[1]] = w[3:]
        elif w[0] == "unfix" and len(w) == 2:
            fixes.pop(w[1], None)

    out_runs, warnings = [], []
    natoms = None
    for i, r in enumerate(runs):
        cols, rows, loop = r["columns"], r["rows"], r["loop"]
        styles = sorted({f[0] for f in r["fixes"].values()})
        dt_ps = r["timestep"] * DT_PS[r["units"]] if r["timestep"] and r["units"] in DT_PS else None
        info = {"index": i, "fixes": styles, "rows": len(rows),
                "steps": loop["steps"] if loop else None,
                "first": dict(zip(cols, rows[0])) if rows else None,
                "last": dict(zip(cols, rows[-1])) if rows else None,
                "timestep_ps": dt_ps}
        if loop:
            natoms = natoms or loop["atoms"]
            if loop["steps"] > 0:
                info["s_per_step"] = loop["time_s"] / loop["steps"]
                if dt_ps:
                    info["ns_per_day"] = dt_ps * 1e-3 * 86400 / info["s_per_step"]
        thermo = [f for f in r["fixes"].values() if f[0] in THERMOSTATS]
        for f in thermo:
            if "temp" in f:
                k = f.index("temp")
                try:
                    info["target_T"] = float(f[k + 2])
                except (IndexError, ValueError):
                    pass
        integ = [f[0] for f in r["fixes"].values() if f[0] in INTEGRATORS]
        info["ensemble"] = ("nve" if integ == ["nve"] and not thermo else
                            integ[0] if len(integ) == 1 else
                            "none" if not integ else "+".join(integ))
        if "Temp" in cols and rows:
            temps = [row[cols.index("Temp")] for row in rows]
            h = len(temps) // 2
            info["T_tail_mean"] = sum(temps[h:]) / max(1, len(temps) - h)
            info["T_range"] = [min(temps), max(temps)]
        if (info["ensemble"] == "nve" and "TotEng" in cols and "Step" in cols and dt_ps
                and len(rows) >= 3 and natoms):
            t = [row[cols.index("Step")] * dt_ps for row in rows]
            e = [row[cols.index("TotEng")] / natoms * 1000 for row in rows]
            if r["units"] == "metal":
                info["nve_drift_meV_atom_ps"], info["nve_fluct_meV_atom"] = _fit(t, e)
            else:
                info["nve_drift_per_atom_ps"], info["nve_fluct_per_atom"] = _fit(t, e)
                info["energy_note"] = f"units {r['units']}: energies x1000 per atom, not meV"
            info["nve_ps"] = t[-1] - t[0]
        if any(not math.isfinite(v) for row in rows for v in row):
            warnings.append(f"run {i}: non-finite thermo values (the system blew up)")
        out_runs.append(info)

    timed = [r for r in out_runs if r.get("s_per_step")]
    nve = [r for r in out_runs if "nve_ps" in r]
    therm = [r for r in out_runs if r.get("target_T") is not None and r["ensemble"] != "nve"]
    first = runs[0] if runs else None
    pe0 = None
    if first and first["rows"] and "PotEng" in first["columns"]:
        pe0 = first["rows"][0][first["columns"].index("PotEng")]
    if not finished:
        warnings.append("no 'Total wall time' line: the run did not finish (killed, walltime, "
                        "or still running)")
    if error:
        warnings.append(f"LAMMPS error: {error}")
    if dangerous:
        warnings.append(f"{dangerous} dangerous neighbor-list builds: atoms moved more than "
                        "half the skin between checks; raise the skin or check every step")
    for r in out_runs:
        if r.get("target_T") and r.get("T_range") and r["rows"] > 10:
            lo, hi = r["T_range"]
            if hi > 3 * r["target_T"]:
                warnings.append(f"run {r['index']}: temperature reached {hi:.0f} K, over 3x the "
                                f"target {r['target_T']:.0f} K")
    warnings += warns[:10]
    moving = [r for r in out_runs if r.get("T_range") and r.get("steps")]   # not `run 0`
    summary = {
        "finished": finished, "error": error, "version": version, "ranks": ranks,
        "threads": threads, "natoms": natoms, "pe0": pe0,
        "s_per_step": timed[-1]["s_per_step"] if timed else None,
        "ns_per_day": timed[-1].get("ns_per_day") if timed else None,
        "T_tail_mean": therm[-1].get("T_tail_mean") if therm else None,
        "T_range": ([min(r["T_range"][0] for r in moving), max(r["T_range"][1] for r in moving)]
                    if moving else None),
        "nve": ({k: nve[-1][k] for k in nve[-1] if k.startswith("nve_") or k == "energy_note"}
                if nve else None),
        "runs": out_runs, "warnings": warnings}
    return summary
