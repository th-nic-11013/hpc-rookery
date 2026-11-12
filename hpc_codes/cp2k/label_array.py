"""Packed CP2K label jobs (G51): K frames per job, one after another.

Reads the batch.json that hyogo's cp2k_make_label_batch writes (the file is
the contract: hpc_codes imports nothing from hyogo) and launch_plan's result,
and writes one driver script per job (pack-NN.sh) plus packs.json into the
batch folder, and a submit_job spec per job. No job array: the scheduler
tools have none, and packing keeps 100-200 frames to a handful of jobs (far
below HOKUSAI's 5,000-job cap) without touching hpc_agent_core.

The driver is restart-safe: a frame whose label.out has PROGRAM ENDED is
skipped, an unfinished label.out is kept as label.out.prev and the frame is
run again, and no frame starts without its time budget left in the job. So
resubmitting the same script runs only what is left.
"""
import json
import math
import re
import shlex

BATCH_KIND = "cp2k-label-batch"
BATCH_VERSION = 1
PACKS = "packs.json"
JOB_MARGIN_S = 300        # job start-up and the final log line
STOP_MARGIN_S = 120       # the driver stops starting frames this long before the job ends
CP2K_BINARY = "cp2k.psmp"
PACK_RE = re.compile(r"pack-(\d+)\.sh")


def check_batch(m) -> dict:
    if not isinstance(m, dict) or m.get("kind") != BATCH_KIND:
        raise ValueError("batch_dir has no label batch: batch.json is written by hyogo's "
                         "cp2k_make_label_batch")
    if m.get("version") != BATCH_VERSION:
        raise ValueError(f"batch.json version {m.get('version')}; this rookery reads {BATCH_VERSION}")
    for k in ("frames", "input", "output", "settings", "wfn"):
        if k not in m:
            raise ValueError(f"batch.json has no {k!r}")
    return m


def _hms(s: int) -> str:
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _seconds(hms: str) -> int:
    h, m, s = (int(x) for x in hms.split(":"))
    return 3600 * h + 60 * m + s


def driver(name: str, index: int, total: int, frames: list[str], launcher: str, budget_s: int,
           duration_s: int, guess: bool, inp: str, out: str, wfn: str, guess_file: str | None,
           binary: str = CP2K_BINARY) -> str:
    """The pack-NN.sh text: run `frames` (folder names) one after another."""
    log = f"pack-{index:02d}.log"
    wfn_q = shlex.quote(wfn)
    lines = [
        "#!/bin/bash",
        f"# {name} pack {index:02d} of {total:02d}: {len(frames)} CP2K label frames, one after "
        "another; written by cp2k_pack_jobs (G51).",
        "# Restart-safe: a frame whose label.out has PROGRAM ENDED is skipped, so resubmitting this",
        f"# script runs only what is left. Per-frame log: {log}. Run from the batch folder.",
        "set -u",
        f"LAUNCH={shlex.quote(launcher)}",
        f"CP2K=\"${{CP2K:-{binary}}}\"",
        f"BUDGET={budget_s}      # s a frame may need (estimate x safety); none starts without it",
        f"LIMIT={duration_s - STOP_MARGIN_S}       # s: the job's duration minus {STOP_MARGIN_S} s",
        f"GUESS={1 if guess else 0}         # 1: start from the previous frame's wavefunction",
        "FRAMES=(" + " ".join(frames) + ")",
        f"log() {{ echo \"$(date '+%F %T') $*\" >> {log}; }}",
        "ended() { [ -f \"$1\" ] && grep -q 'PROGRAM ENDED' \"$1\"; }",
        "log \"start: job ${SLURM_JOB_ID:-${PBS_JOBID:-?}} on $(hostname), ${#FRAMES[@]} frames\"",
        "prev=\"\"",
        "for f in \"${FRAMES[@]}\"; do",
        f"  out=\"$f/{out}\"",
        "  if ended \"$out\"; then log \"$f skip: finished\"; prev=$f; continue; fi",
        "  if (( SECONDS + BUDGET > LIMIT )); then",
        "    log \"stop before $f: $((LIMIT - SECONDS)) s left, $BUDGET s needed\"; break",
        "  fi",
        "  [ -f \"$out\" ] && mv -f \"$out\" \"$out.prev\"",
    ]
    if guess:
        lines += [
            f"  rm -f \"$f/{guess_file}\"",
            f"  if [ -n \"$prev\" ] && [ -f \"$prev\"/{wfn_q} ]; then "
            f"cp \"$prev\"/{wfn_q} \"$f/{guess_file}\"; fi",
        ]
    lines += [
        "  t0=$SECONDS",
        f"  ( cd \"$f\" && $LAUNCH \"$CP2K\" -i {shlex.quote(inp)} -o {shlex.quote(out)} )",
        "  rc=$?",
        "  if ended \"$out\"; then st=ended; else st=unfinished; fi",
        "  log \"$f rc=$rc $st $((SECONDS - t0)) s\"",
    ]
    if guess:   # keep this frame's wavefunction for the next one only
        lines += [
            f"  rm -f \"$f/{guess_file}\" \"$f\"/{wfn_q}.bak-*",
            "  if [ -n \"$prev\" ]; then rm -f \"$prev\"/" + wfn_q + "; fi",
        ]
    else:       # ~40 MB per 456-atom frame: not kept
        lines.append("  rm -f \"$f\"/" + wfn_q + " \"$f\"/" + wfn_q + ".bak-*")
    lines += [
        "  prev=$f",
        "done",
        "n=0; for f in \"${FRAMES[@]}\"; do ended \"$f/" + out + "\" && n=$((n + 1)); done",
        "log \"end: $n of ${#FRAMES[@]} frames ended\"",
        "",
    ]
    return "\n".join(lines)


def pack(batch: dict, plan: dict, remote_dir: str, name: str, frames: list[str] | None = None,
         frames_per_job: int | None = None, max_hours: float = 24.0, safety: float = 1.5,
         frame_s: float | None = None, first_index: int = 1) -> dict:
    """Packs ({index, frames, script name, script text, spec}) and a summary."""
    m = check_batch(batch)
    all_names = [f["name"] for f in m["frames"]]
    if frames is None:
        chosen = all_names
    else:
        unknown = sorted(set(frames) - set(all_names))
        if unknown:
            raise ValueError(f"frames not in this batch: {unknown[:8]}")
        chosen = [n for n in all_names if n in set(frames)]
    if not chosen:
        raise ValueError("no frames to pack")
    if not isinstance(plan, dict) or not isinstance(plan.get("job_spec"), dict):
        raise ValueError("plan is launch_plan's result for this batch (code cp2k, run_type "
                         "single_point, the frames' atoms and method)")
    js = plan["job_spec"]
    for k in ("launcher", "resources"):
        if not js.get(k):
            raise ValueError(f"plan.job_spec has no {k}: pass launch_plan's whole result")
    warnings = []
    est = plan.get("estimate") or {}
    if frame_s is None:
        if not est.get("value") or est.get("metric") != "wall_s":
            raise ValueError("launch_plan gave no single-point time for this system: benchmark it, "
                             "or pass frame_s (seconds per frame, e.g. cp2k_collect_labels' "
                             "wall_s.max from an earlier batch)")
        frame_s = float(est["value"])
        if est.get("rough"):
            warnings.append(f"the {frame_s:.0f} s per frame is rough (launch_plan basis "
                            f"{(plan.get('basis') or {}).get('match')}); the safety factor "
                            f"{safety} covers it only if it is not far off")
    if frame_s <= 0 or safety < 1:
        raise ValueError("frame_s > 0 and safety >= 1")
    if not 0 < max_hours <= 24 * 7:
        raise ValueError("max_hours is the longest job to request, in hours")
    budget = math.ceil(frame_s * safety)
    cap = int(max_hours * 3600)
    k_max = (cap - JOB_MARGIN_S - STOP_MARGIN_S) // budget
    if k_max < 1:
        raise ValueError(f"one frame ({budget} s with the safety factor) does not fit a "
                         f"{max_hours} h job")
    if frames_per_job is not None:
        if frames_per_job < 1:
            raise ValueError("frames_per_job >= 1")
        if frames_per_job > k_max:
            raise ValueError(f"{frames_per_job} frames x {budget} s do not fit a {max_hours} h job "
                             f"with margin; at most {k_max}")
    k = frames_per_job or k_max
    jobs = math.ceil(len(chosen) / k)
    k = math.ceil(len(chosen) / jobs)          # balance: the last job is not a stub
    duration = min(cap, math.ceil((k * budget + JOB_MARGIN_S + STOP_MARGIN_S) / 900) * 900)
    walltime = m["settings"].get("frame_duration")
    if walltime and _seconds(walltime) - 600 < budget:
        warnings.append(f"the inputs' per-frame CP2K WALLTIME ({_seconds(walltime) - 600} s) is "
                        f"below the {budget} s budget: a normal frame may be stopped. Rewrite the "
                        "batch with a longer frame_duration")
    guess = m["settings"].get("guess") == "previous"
    res = js["resources"]
    nodes = int(res.get("node_count") or 1)
    cores = nodes * int(res.get("processes_per_node") or 1) * int(res.get("cpu_cores_per_process") or 1)
    packs = []
    for j in range(jobs):
        idx = first_index + j
        names = chosen[j * k:(j + 1) * k]
        script = f"pack-{idx:02d}.sh"
        text = driver(name, idx, first_index + jobs - 1, names, js["launcher"], budget, duration,
                      guess, m["input"], m["output"], m["wfn"], m.get("guess_file"))
        spec = {"name": f"{name}-p{idx:02d}", "directory": remote_dir,
                "executable": f"bash {script}",
                "pre_launch": js.get("pre_launch"), "environment": dict(js.get("environment") or {}),
                "resources": dict(res),
                "attributes": {**(js.get("attributes") or {}), "duration": _hms(duration)}}
        packs.append({"index": idx, "script": script, "text": text, "frames": names, "spec": spec})
    if "agent/work/" not in remote_dir:
        warnings.append(f"{remote_dir} is not under ~/agent/work/, where agent runs belong")
    total_h = len(chosen) * frame_s * cores / 3600
    summary = {"frames": len(chosen), "jobs": jobs, "frames_per_job": k, "frame_s": frame_s,
               "budget_s": budget, "duration": _hms(duration),
               "core_hours": {"expected": round(total_h, 1),
                              "requested": round(jobs * duration * cores / 3600, 1)},
               "guess": "previous" if guess else "atomic", "warnings": warnings}
    return {"packs": packs, **summary}
