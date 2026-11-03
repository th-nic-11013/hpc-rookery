"""Summarise the CP2K layout benchmark runs.

Standard library only: the benchmark job runs it on compute nodes, and the
hpc-codes tool cp2k_bench_parse sends this same file to the login node.

Usage (text parsing only, fine on the front end). Every form takes
--cores=N (cores per node; default $CP2K_CORES_PER_NODE) before the rest:
    python3 -I parse_bench.py --cores=N [runs/<jobid>/<stage> ...]
        Without arguments it reads every runs/<jobid>/stage*/ folder next to this script.
        Prints a Markdown table per input. Only the no-argument (all runs) form writes
        results.csv next to this script, so a job's own summary never overwrites it.
    parse_bench.py --check runs/<jobid>/<stage>
        Exit 0 only if every run there finished (PROGRAM ENDED) and agrees in energy
        (dE_max <= 1e-6 Ha). Used as the gate between stages.
    parse_bench.py --pick N runs/<jobid>/<stage>
        Print the N fastest full-node layouts there as "RxT RxT ...", best first,
        counting only finished runs that agree in energy. Exit 1 if there is none.
    parse_bench.py --stats RxT runs/<jobid>/<stage>
        Print "<s_per_step> <first_step_s> <md_steps>" of the finished run of layout
        RxT there (rep 1). Used to predict the run time of a larger follow-up job.
    parse_bench.py --json runs/<jobid>/<stage> ...
        Print one JSON object: every run's metrics (no energy or force arrays) and,
        per stage folder, whether it passes --check and its --pick ranking.

Per run (folder <input>_<R>x<T>_r<rep>/cp2k.out):
  s_per_step  mean "MD| CPU time per MD step" over MD steps >= 3
  cp2k_s      total time of the CP2K timer (max over ranks)
  grid/dbcsr/fft_s  grid_*_task_list self time, dbcsr_multiply_generic and
              fft_wrap_pw1pw2 total time (max over ranks). PRINT_LEVEL LOW prints no
              MPI timers, so communication shows only as the rest of s_per_step.
  peak_mib    largest "Estimated peak process memory" (per rank)
  dE_max      largest |E - E_ref| over the MD energies, against the first finished
              run of the same input (should be <= 1e-6 Ha)
  scf_nc      SCF runs NOT converged after the first force evaluation (the first one
              starts from the atomic guess and may legitimately hit MAX_SCF). Any
              later one makes the step time meaningless: the gate fails on it.
  scf_iters   SCF (OT or diagonalisation) iterations, all force evaluations
  dF_max      largest |F - F_ref| over all atomic force components (Ha/bohr), when
              the run prints forces (&FORCE_EVAL &PRINT &FORCES ON), against the
              first finished run of the same input (gate: <= 1e-4 Ha/bohr)

Single points (RUN_TYPE ENERGY or ENERGY_FORCE: no MD steps in cp2k.out) are
detected automatically: s_per_step and first_s are then the CP2K time of the whole
single point, steps is 0, and scf_nc counts the (only) SCF too, so --check, --pick
and --stats work unchanged on single-point stages.
"""
import csv
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = re.compile(r"^(?P<inp>.+)_(?P<R>\d+)x(?P<T>\d+)_r(?P<rep>\d+)$")
NODE_CORES = int(os.environ.get("CP2K_CORES_PER_NODE") or 0)   # --cores=N overrides
ITER = re.compile(r"^\s+\d+\s+(OT\s|\S*Diag\.)")
FORCES_TAG = re.compile(r"^ FORCES\|\s+\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s*$")   # 2026.x screen format
FORCE = re.compile(r"^\s+\d+\s+\d+\s+[A-Z][a-z]?\s+(-?\d+\.\d+(?:[EDed][-+]?\d+)?)\s+(-?\d+\.\d+(?:[EDed][-+]?\d+)?)\s+(-?\d+\.\d+(?:[EDed][-+]?\d+)?)\s*$")


def parse_out(path):
    step_t, energies, peak, timers = [], [], 0, {}
    done = False
    scf_nc = scf_all_nc = scf_iters = 0
    forces, in_forces = [], False
    for line in path.read_text(errors="replace").splitlines():
        if in_forces:
            m = FORCE.match(line)
            if m:
                forces.extend(float(x.replace("D", "E").replace("d", "e")) for x in m.groups())
                continue
            if "SUM OF ATOMIC FORCES" in line:
                in_forces = False
                continue
        if line.startswith(" FORCES|"):
            m = FORCES_TAG.match(line)
            if m:
                forces.extend(float(x) for x in m.groups())
            elif " forces [" in line:      # header of a new force printout
                forces = []
            continue
        if "ATOMIC FORCES in [a.u.]" in line:   # older screen format
            in_forces, forces = True, []
        elif ITER.match(line):
            scf_iters += 1
        elif line.startswith(" MD| CPU time per MD step [s]"):
            step_t.append(float(line.split()[-2]))
        elif line.startswith(" ENERGY| Total FORCE_EVAL ( QS ) energy [hartree]"):
            energies.append(float(line.split()[-1]))
        elif "Estimated peak process memory after this step [MiB]" in line:
            peak = max(peak, int(line.split()[-1]))
        elif "PROGRAM ENDED AT" in line:
            done = True
        elif "SCF run NOT converged" in line:
            scf_all_nc += 1
            if energies:          # not the first force evaluation
                scf_nc += 1
        else:
            # timing report: name calls asd self_avg self_max total_avg total_max
            f = line.split()
            if len(f) == 7 and re.fullmatch(r"[A-Za-z_][\w]*", f[0]) and f[1].isdigit():
                try:
                    timers[f[0]] = (float(f[4]), float(f[6]))  # (self_max, total_max)
                except ValueError:
                    pass
    steady = step_t[2:] or step_t
    cp2k_s = timers.get("CP2K", (None, None))[1]
    sp = not step_t                # single point: time the whole run
    return {
        "done": done,
        "sp": sp,
        "steps": len(step_t),
        "s_per_step": cp2k_s if sp else (sum(steady) / len(steady) if steady else None),
        "first_s": cp2k_s if sp else (step_t[0] if step_t else None),
        "cp2k_s": cp2k_s,
        "grid_s": sum(v[0] for k, v in timers.items() if k.startswith("grid_") and k.endswith("_task_list")),
        "dbcsr_s": timers.get("dbcsr_multiply_generic", (0, 0))[1],
        "fft_s": timers.get("fft_wrap_pw1pw2", (0, 0))[1],
        "peak_mib": peak or None,
        "scf_nc": scf_all_nc if sp else scf_nc,
        "scf_iters": scf_iters,
        "energies": energies,
        "forces": forces,
    }


def fmt(x, spec):
    return "–" if x is None else format(x, spec)


E_TOL = 1e-6  # Ha; layouts converge the SCF along different numerical paths
F_TOL = 1e-4  # Ha/bohr, largest force component difference between layouts


def collect(jobdirs):
    rows = []
    for jd in jobdirs:
        if not jd.is_dir():
            continue
        for rd in sorted(p for p in jd.iterdir() if p.is_dir()):
            m = NAME.match(rd.name)
            out = rd / "cp2k.out"
            if not m or not out.exists():
                continue
            r = parse_out(out)
            r.update(job=f"{jd.parent.name}/{jd.name}", input=m["inp"], R=int(m["R"]), T=int(m["T"]), rep=int(m["rep"]))
            rows.append(r)

    # energy agreement against the first finished run of each input
    ref, fref = {}, {}
    for r in rows:
        if r["done"] and r["input"] not in ref:
            ref[r["input"]] = r["energies"]
            fref[r["input"]] = r["forces"]
    for r in rows:
        e0 = ref.get(r["input"])
        n = min(len(e0), len(r["energies"])) if e0 else 0
        r["dE_max"] = max(abs(a - b) for a, b in zip(e0[:n], r["energies"][:n])) if n else None
        f0 = fref.get(r["input"]) or []
        same = f0 and len(f0) == len(r["forces"])
        r["dF_max"] = max(abs(a - b) for a, b in zip(f0, r["forces"])) if same else None
        r["ok"] = (r["done"] and r["dE_max"] is not None and r["dE_max"] <= E_TOL
                   and r["scf_nc"] == 0
                   and (not f0 or (r["dF_max"] is not None and r["dF_max"] <= F_TOL)))
    return rows


def check(jobdir):
    rows = collect([Path(jobdir)])
    bad = [r for r in rows if not r["ok"]]
    for r in rows:
        print(f"{r['input']} {r['R']}x{r['T']} r{r['rep']}: done={r['done']} dE_max={fmt(r['dE_max'], '.1e')}"
              f" dF_max={fmt(r['dF_max'], '.1e')} scf_iters={r['scf_iters']}"
              f" scf_not_converged={r['scf_nc']}"
              f"{'' if r['ok'] else '  <-- FAIL'}")
    if not rows:
        print(f"no runs found in {jobdir}")
    return 0 if rows and not bad else 1


def ranked(rows):
    """Full-node layouts of usable runs, fastest first."""
    best = {}
    for r in sorted((r for r in rows if r["ok"] and r["s_per_step"] and r["R"] * r["T"] == NODE_CORES),
                    key=lambda r: r["s_per_step"]):
        best.setdefault(f"{r['R']}x{r['T']}", r["s_per_step"])
    return list(best)


def pick(n, jobdir):
    best = ranked(collect([Path(jobdir)]))
    if not best:
        return 1
    print(" ".join(best[:n]))
    return 0


def as_json(jobdirs):
    keep = ["job", "input", "R", "T", "rep", "done", "sp", "steps", "s_per_step", "first_s",
            "cp2k_s", "grid_s", "dbcsr_s", "fft_s", "peak_mib", "dE_max", "dF_max",
            "scf_nc", "scf_iters", "ok"]
    runs, stages = [], {}
    for jd in jobdirs:
        rows = collect([Path(jd)])
        runs += [{k: r[k] for k in keep} for r in rows]
        stages[jd] = {"found": Path(jd).is_dir(), "runs": len(rows),
                      "passed": bool(rows) and all(r["ok"] for r in rows),
                      "ranked": ranked(rows)}
    print(json.dumps({"node_cores": NODE_CORES, "runs": runs, "stages": stages}))
    return 0


def main(argv):
    global NODE_CORES
    if argv[:1] == ["--cores"] and len(argv) > 1:
        argv = [f"--cores={argv[1]}"] + argv[2:]
    if argv and argv[0].startswith("--cores="):
        NODE_CORES = int(argv[0].split("=", 1)[1])
        argv = argv[1:]
    if not NODE_CORES and argv[:1] != ["--check"]:
        sys.exit("parse_bench.py: give --cores=N (cores per node) or set CP2K_CORES_PER_NODE")
    if argv[:1] == ["--json"] and len(argv) > 1:
        return as_json(argv[1:])
    if argv[:1] == ["--check"] and len(argv) == 2:
        return check(argv[1])
    if argv[:1] == ["--stats"] and len(argv) == 3:
        R, T = (int(x) for x in argv[1].split("x"))
        hit = [r for r in collect([Path(argv[2])]) if r["R"] == R and r["T"] == T and r["rep"] == 1
               and r["done"] and r["s_per_step"]]
        if not hit:
            return 1
        print(f"{hit[0]['s_per_step']:.3f} {hit[0]['first_s']:.3f} {hit[0]['steps']}")
        return 0
    if argv[:1] == ["--pick"] and len(argv) == 3:
        return pick(int(argv[1]), argv[2])
    jobdirs = [Path(a) for a in argv] or sorted(p for p in (HERE / "runs").glob("*/stage*") if p.is_dir())
    rows = collect(jobdirs)

    cols = ["job", "input", "R", "T", "rep", "done", "steps", "s_per_step", "cp2k_s",
            "grid_s", "dbcsr_s", "fft_s", "peak_mib", "dE_max", "scf_nc", "scf_iters", "dF_max"]
    if not argv:
        with open(HERE / "results.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    for inp in sorted({r["input"] for r in rows}):
        sel = [r for r in rows if r["input"] == inp]
        full = [r for r in sel if r["R"] * r["T"] == NODE_CORES and r["done"] and r["s_per_step"]]
        base = next((r["s_per_step"] for r in full if r["T"] == 1), None)
        sp = any(r["sp"] for r in sel)
        unit = "s/SP" if sp else "s/step"
        print(f"\n### {inp}{' (single point: time per single point)' if sp else ''}\n")
        print(f"| job | R×T | rep | done | {unit} | vs {NODE_CORES}×1 | core-h/{unit[2:]} | CP2K s | grid | dbcsr | fft | MiB/rank | dE max | SCF nc | SCF it | dF max |")
        print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in sorted(sel, key=lambda r: (r["s_per_step"] is None, r["s_per_step"] or 0)):
            cores = r["R"] * r["T"]
            speed = base / r["s_per_step"] if base and r["done"] and r["s_per_step"] and cores == NODE_CORES else None
            ch = cores * r["s_per_step"] / 3600 if r["s_per_step"] else None
            print(f"| {r['job']} | {r['R']}×{r['T']} | {r['rep']} | {'yes' if r['done'] else 'NO'} "
                  f"| {fmt(r['s_per_step'], '.2f')} | {fmt(speed, '.2f')} | {fmt(ch, '.4f')} "
                  f"| {fmt(r['cp2k_s'], '.1f')} | {r['grid_s']:.1f} | {r['dbcsr_s']:.1f} | {r['fft_s']:.1f} "
                  f"| {fmt(r['peak_mib'], 'd')} | {fmt(r['dE_max'], '.1e')} | {r['scf_nc']} "
                  f"| {r['scf_iters']} | {fmt(r['dF_max'], '.1e')} |")
    if not argv:
        print(f"\nWrote {HERE / 'results.csv'} ({len(rows)} runs)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
