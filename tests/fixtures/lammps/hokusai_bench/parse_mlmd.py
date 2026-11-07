"""Read and check the LAMMPS logs of the MLMD benchmark; plan run lengths.

Run folders are runs/<jobid>/<stage>/<system>_<prec>_<R>x<T>_<mode>_r<rep>/log.lammps,
with status.tsv (exit code, wall time) in each stage folder. Python standard library
only, so it runs anywhere (front end included):

  parse_mlmd.py plan <run dir> <target_s|same> <factor> [--atoms N] [--min-steps M] [--warm W]
      From a finished time-mode run: "<nwarm> <nsteps> <timeout_s>" so that the timed
      part lasts about target_s ("same": the reference run's own step counts). The timeout allows the reference run's start-up
      overhead plus <factor> x the expected MD time (slower layouts, other precision).
      --atoms N scales the step time linearly to a system of N atoms.
  parse_mlmd.py scan-target <stage1 dir> <budget_s> <layouts> <cap_s> <system>...
      Seconds of timed MD per stage-2 run (5 to cap_s) so that all layouts of all
      systems fit in budget_s, from the stage-1 step times and start-up overheads.
  parse_mlmd.py valid-plan <run dir> <nvt_s> <nve_s> [--speedup x]
      "<nnvt> <nnve> <timeout_s>" for a validation run (NVT 100-1000, NVE 200-2000 steps).
  parse_mlmd.py check <stage dir> [--refs refs.tsv]
      Gate for every finished run: step-0 energy matches the ASE reference (f64 1e-6,
      f32 1e-4 eV/atom; the large systems have none: float32 is compared with float64) and the other layouts of the same system and precision;
      thermo values finite; validation runs stay between 50 and 1000 K. Exit 1 on any
      failure.
  parse_mlmd.py pick <stage dir> <system> <prec> [n]
      The n (default 1) fastest layouts (RxT) among the passed time-mode runs.
  parse_mlmd.py [summary] [<runs/jobid dir or stage dir>...]
      Tables (all runs/* by default) and results.csv in the bench folder.
"""
import csv
import math
import re
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent
DT_FS = 0.5
TOL = {"f64": 1e-6, "f32": 1e-4}            # eV/atom
CP2K = {"h2o-64": 1.80, "h2o-128": 3.56, "uio66-prim": 5.14, "uio66-conv": 9.34}  # s/step, 16x7, revPBE-D3

LOOP = re.compile(r"^Loop time of (\S+) on (\d+) procs for (\d+) steps with (\d+) atoms")
TASKS = re.compile(r"(\d+) MPI tasks x (\d+) OpenMP threads")


def parse_name(d):
    sysname, prec, layout, mode, rep = d.name.split("_")
    R, T = (int(x) for x in layout.split("x"))
    return dict(system=sysname, prec=prec, R=R, T=T, layout=layout, mode=mode, rep=int(rep[1:]))


def parse_log(path):
    """Thermo blocks, loop timings, thread count and completion of one log.lammps."""
    out = dict(blocks=[], loops=[], threads=None, done=False, error=None)
    if not path.exists():
        return out
    header = None
    for line in path.read_text(errors="replace").splitlines():
        w = line.split()
        if w[:1] == ["Step"]:
            header, rows = w, []
            out["blocks"].append((header, rows))
            continue
        m = LOOP.match(line)
        if m:
            out["loops"].append(dict(time=float(m[1]), procs=int(m[2]), steps=int(m[3]), atoms=int(m[4])))
            header = None
            continue
        if header and len(w) == len(header):
            try:
                rows.append([float(x) for x in w])
            except ValueError:
                header = None
            continue
        m = TASKS.search(line)
        if m:
            out["threads"] = int(m[2])
        if line.startswith("ERROR"):
            out["error"] = out["error"] or line.strip()
        if line.startswith("Total wall time"):
            out["done"] = True
    return out


def col(block, name):
    header, rows = block
    i = header.index(name)
    return [r[i] for r in rows]


def status_of(stage):
    st = {}
    f = stage / "status.tsv"
    if f.exists():
        for r in csv.DictReader(f.open(), delimiter="\t"):
            key = f"{r['system']}_{r['prec']}_{r['ranks']}x{r['threads']}_{r['mode']}_r{r['rep']}"
            st[key] = (int(r["exit"]), int(r["wall_s"]))
    return st


def run_info(d, st=None):
    """Everything known about one run folder."""
    info = parse_name(d)
    log = parse_log(d / "log.lammps")
    if st is None:
        st = status_of(d.parent)
    info["exit"], info["wall"] = st.get(d.name, (None, None))
    info["done"], info["error"] = log["done"], log["error"]
    info["atoms"] = log["loops"][0]["atoms"] if log["loops"] else None
    info["pe0"] = col(log["blocks"][0], "PotEng")[0] if log["blocks"] and log["blocks"][0][1] else None
    info["log"] = log
    info["ok"] = info["exit"] == 0 and info["done"] and info["pe0"] is not None
    if info["mode"] == "time" or info["mode"].startswith("conc"):
        timed = [l for l in log["loops"] if l["steps"] > 0]
        if len(timed) >= 2:                       # [warm-up, timed]
            last = timed[-1]
            info["steps"] = last["steps"]
            info["t_step"] = last["time"] / last["steps"]
            info["t_warm"] = timed[-2]["time"] / timed[-2]["steps"]
            info["ns_day"] = DT_FS * 1e-6 * 86400 / info["t_step"]
    elif info["mode"] == "valid" and len(log["blocks"]) >= 3:
        nvt, nve = log["blocks"][1], log["blocks"][2]
        temps = col(nvt, "Temp")
        info["T_nvt"] = sum(temps[len(temps) // 2:]) / max(1, len(temps) - len(temps) // 2)
        info["T_range"] = (min(temps + col(nve, "Temp")), max(temps + col(nve, "Temp")))
        steps, etot = col(nve, "Step"), col(nve, "TotEng")
        n = info["atoms"] or 1
        if len(steps) >= 3:
            t = [s * DT_FS / 1000 for s in steps]           # ps
            e = [x / n * 1000 for x in etot]                 # meV/atom
            tm, em = sum(t) / len(t), sum(e) / len(e)
            slope = sum((a - tm) * (b - em) for a, b in zip(t, e)) / sum((a - tm) ** 2 for a in t)
            info["drift"] = slope                            # meV/atom/ps
            info["e_std"] = math.sqrt(sum((b - em - slope * (a - tm)) ** 2 for a, b in zip(t, e)) / len(e))
            info["nve_ps"] = t[-1] - t[0]
        loops = [l for l in log["loops"] if l["steps"] > 0]
        if loops:
            info["t_step"] = loops[-1]["time"] / loops[-1]["steps"]
            info["ns_day"] = DT_FS * 1e-6 * 86400 / info["t_step"]
    return info


def runs_in(stage):
    st = status_of(stage)
    return [run_info(d, st) for d in sorted(stage.iterdir()) if d.is_dir() and d.name.count("_") == 4]


def refs_table(path):
    if not path or not Path(path).exists():
        return {}
    return {(r["name"], r["prec"]): float(r["energy_eV"])
            for r in csv.DictReader(open(path), delimiter="\t")}


# ---------------------------------------------------------------- commands
def cmd_plan(args):
    d, target, factor = Path(args[0]), args[1], float(args[2])
    opts = dict(zip(args[3::2], args[4::2]))
    r = run_info(d)
    if "t_step" not in r:
        sys.exit(f"plan: no timing in {d}")
    t = r["t_step"]
    if "--atoms" in opts:
        t *= int(opts["--atoms"]) / r["atoms"]
    if target == "same":                     # repeat the reference run's lengths
        loops = [l for l in r["log"]["loops"] if l["steps"] > 0]
        nwarm, nsteps = loops[-2]["steps"], loops[-1]["steps"]
    else:
        nwarm = int(opts.get("--warm", 10))
        nsteps = max(int(opts.get("--min-steps", 10)), min(5000, round(float(target) / t)))
    # start-up overhead of the reference run: wall time minus its MD loops
    md = sum(l["time"] for l in r["log"]["loops"])
    overhead = max(20.0, (r["wall"] or 0) - md)
    tmo = round(overhead + 30 + factor * (nwarm + nsteps + 1) * t)
    print(nwarm, nsteps, max(90, tmo))


def cmd_scan_target(args):
    """Seconds of timed MD per stage-2 run so that the whole scan fits in budget_s."""
    s1, budget, nlay, cap = Path(args[0]), float(args[1]), int(args[2]), float(args[3])
    systems = args[4:]
    fixed, nsys = 0.0, 0
    for s in systems:
        r = run_info(s1 / f"{s}_f64_1x28_time_r1")
        if "t_step" not in r:
            sys.exit(f"scan-target: no stage-1 timing for {s}")
        md = sum(l["time"] for l in r["log"]["loops"])
        overhead = max(20.0, (r["wall"] or 0) - md)
        fixed += overhead + 1.5 * 11 * r["t_step"]       # start-up, warm-up (slower layouts ~1.5x)
        nsys += 1
    target = (budget / nlay - fixed) / (1.5 * nsys)
    print(f"{max(5.0, min(cap, target)):.1f}")


def cmd_valid_plan(args):
    d, nvt_s, nve_s = Path(args[0]), float(args[1]), float(args[2])
    opts = dict(zip(args[3::2], args[4::2]))
    r = run_info(d)
    if "t_step" not in r:
        sys.exit(f"valid-plan: no timing in {d}")
    t = r["t_step"] / float(opts.get("--speedup", 1))
    nnvt = max(100, min(1000, round(nvt_s / t)))
    nnve = max(200, min(2000, round(nve_s / t)))
    md = sum(l["time"] for l in r["log"]["loops"])
    overhead = max(20.0, (r["wall"] or 0) - md)
    print(nnvt, nnve, max(120, round(overhead + 30 + 2 * (nnvt + nnve + 1) * t)))


def cmd_check(args):
    stage = Path(args[0])
    opts = dict(zip(args[1::2], args[2::2]))
    refs = refs_table(opts.get("--refs", BENCH / "refs.tsv"))
    bad = 0
    first = {}
    large = {l.split("\t")[0] for l in (BENCH / "structures/systems.tsv").read_text().splitlines()[1:]
             if l.split("\t")[3] == "large"} if (BENCH / "structures/systems.tsv").exists() else set()
    rs = runs_in(stage)
    for r in rs:
        if r["exit"] == 125 or r["exit"] is None:
            continue                        # not started (time guard) or still running
        tag = f"{r['system']} {r['prec']} {r['layout']} {r['mode']} r{r['rep']}"
        if not r["ok"]:
            print(f"FAIL {tag}: exit {r['exit']}, finished {r['done']} {r['error'] or ''}")
            bad += 1
            continue
        n, tol = r["atoms"], TOL[r["prec"]]
        msgs = []
        ref = refs.get((r["system"], r["prec"]))
        if ref is None and r["system"] in large:
            f64 = [q for q in rs if q["system"] == r["system"] and q["prec"] == "f64" and q["ok"]]
            if r["prec"] == "f32" and f64:      # no ASE reference: float32 against float64
                ref = f64[0]["pe0"]
                print(f"note {tag}: no ASE reference, compared with the float64 run")
            else:
                print(f"note {tag}: no ASE reference, cross-layout check only")
        elif ref is None:
            msgs.append("no ASE reference")
        if ref is not None and abs(r["pe0"] - ref) / n > tol:
            msgs.append(f"step-0 energy {r['pe0']:.6f} vs ASE {ref:.6f} eV ({abs(r['pe0'] - ref) / n:.1e} eV/atom)")
        key = (r["system"], r["prec"])
        first.setdefault(key, r)
        if abs(r["pe0"] - first[key]["pe0"]) / n > tol:
            msgs.append(f"step-0 energy differs from {first[key]['layout']} by "
                        f"{abs(r['pe0'] - first[key]['pe0']) / n:.1e} eV/atom")
        vals = [v for b in r["log"]["blocks"] for row in b[1] for v in row]
        if not all(math.isfinite(v) for v in vals):
            msgs.append("non-finite thermo values")
        if r["mode"] == "valid" and "T_range" in r and not (50 < r["T_range"][0] and r["T_range"][1] < 1000):
            msgs.append(f"temperature left 50-1000 K: {r['T_range'][0]:.0f}-{r['T_range'][1]:.0f} K")
        if msgs:
            bad += 1
            print(f"FAIL {tag}: " + "; ".join(msgs))
        else:
            dref = f"{abs(r['pe0'] - ref) / n:.1e}" if ref is not None else "-"
            print(f"ok   {tag}: step-0 energy {r['pe0']:.6f} eV, |dE| vs ASE {dref} eV/atom")
    sys.exit(1 if bad else 0)


def cmd_pick(args):
    stage, system, prec = Path(args[0]), args[1], args[2]
    n = int(args[3]) if len(args) > 3 else 1
    rs = [r for r in runs_in(stage) if r["system"] == system and r["prec"] == prec
          and r["mode"] == "time" and r["ok"] and "t_step" in r]
    if not rs:
        sys.exit(f"pick: no passed time run of {system} {prec} in {stage}")
    print(" ".join(r["layout"] for r in sorted(rs, key=lambda r: r["t_step"])[:n]))


def cmd_summary(args):
    stages = []
    for a in (args or sorted(str(p) for p in (BENCH / "runs").glob("*"))):
        p = Path(a)
        stages += [p] if (p / "status.tsv").exists() else sorted(q for q in p.glob("stage*") if q.is_dir())
    rows = []
    for stage in stages:
        rs = runs_in(stage)
        if not rs:
            continue
        print(f"\n## {stage.relative_to(BENCH) if stage.is_relative_to(BENCH) else stage}")
        base = {}
        for r in rs:
            if r["mode"] == "time" and "t_step" in r and r["layout"] == "1x28":
                base[(r["system"], r["prec"])] = r["t_step"]
        print(f"{'system':11s} {'prec':4s} {'layout':7s} {'mode':9s} {'rep':>3s} {'exit':>4s} {'steps':>6s} "
              f"{'ms/step':>9s} {'ns/day':>8s} {'vs 1x28':>7s} {'vs CP2K':>8s}  notes")
        for r in rs:
            ms = f"{r['t_step'] * 1000:9.1f}" if "t_step" in r else f"{'-':>9s}"
            nsd = f"{r['ns_day']:8.3f}" if "ns_day" in r else f"{'-':>8s}"
            b = base.get((r["system"], r["prec"]))
            vs = f"{b / r['t_step']:6.2f}x" if b and "t_step" in r and r["mode"] == "time" else f"{'-':>7s}"
            cp = CP2K.get(r["system"])
            vcp = f"{cp / r['t_step']:7.0f}x" if cp and "t_step" in r else f"{'-':>8s}"
            note = ""
            if r["mode"] == "valid" and "drift" in r:
                note = (f"NVT tail {r['T_nvt']:.0f} K; NVE {r['nve_ps']:.2f} ps, drift {r['drift']:+.3f} "
                        f"meV/atom/ps, fluct {r['e_std']:.3f} meV/atom")
            if r["exit"] == 125:
                note = "not started (time guard)"
            elif r["exit"] not in (0, None):
                note = f"FAILED {r['error'] or ''}"
            print(f"{r['system']:11s} {r['prec']:4s} {r['layout']:7s} {r['mode']:9s} {r['rep']:3d} "
                  f"{str(r['exit']):>4s} {r.get('steps', '-')!s:>6s} {ms} {nsd} {vs} {vcp}  {note}")
            rows.append(dict(stage=str(stage.relative_to(BENCH)) if stage.is_relative_to(BENCH) else str(stage),
                             system=r["system"], atoms=r["atoms"], prec=r["prec"], layout=r["layout"],
                             mode=r["mode"], rep=r["rep"], exit=r["exit"], steps=r.get("steps"),
                             s_per_step=r.get("t_step"), ns_per_day=r.get("ns_day"),
                             drift_meV_atom_ps=r.get("drift"), nve_ps=r.get("nve_ps"), T_nvt=r.get("T_nvt")))
        conc = {}
        for r in rs:
            if r["mode"].startswith("conc") and "ns_day" in r:
                conc.setdefault((r["system"], r["prec"], r["layout"], r["mode"].split("-")[0]), []).append(r["ns_day"])
        for (s, p, lay, m), v in conc.items():
            print(f"throughput {s} {p}: {len(v)} x {lay} at once = {sum(v):.3f} ns/day in total "
                  f"({min(v):.3f}-{max(v):.3f} each)")
    if rows and not args:
        with open(BENCH / "results.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {BENCH / 'results.csv'}")


if __name__ == "__main__":
    a = sys.argv[1:]
    cmds = {"plan": cmd_plan, "scan-target": cmd_scan_target, "valid-plan": cmd_valid_plan, "check": cmd_check, "pick": cmd_pick,
            "summary": cmd_summary}
    if a and a[0] in cmds:
        cmds[a[0]](a[1:])
    else:
        cmd_summary(a)
