"""Campaign status (G33): a many-frame "fleet" campaign's per-frame state,
rebuilt from its directory tree on the cluster in one read-only command.

A fleet runs N independent calculations ("frames") of a campaign as one
restartable scheduler job: one lane per node, each lane claiming the next
frame with an atomic `mkdir <frame>/.running` (the claiming job id in
`.running/jobid`), counting tries in `<frame>/.attempts`, writing the job
that last tried it to `<frame>/.lastjob`, and retiring a frame by moving
it into a state folder. The tree, under the campaign root:

    <group>/<frame>/               pending, or running while .running exists
    completed/<group>/<frame>/     done
    failed/<group>/<frame>/        failed (the fleet never revisits it), REASON says why
    scf_hold/<group>/<frame>/      held by hand (the fleet ignores it)

`LAYOUT` holds those names, the defaults of codes/common/campaign's fleet
driver (scripts/run_fleet.sh); `layout=` overrides any of them.

One command reads it all: a bash glob lists the frame folders, one
`find -maxdepth 1` prints the marker files' times and sizes, one awk reads
the small ones (counters, the reason, the SCF step count, the SCF limit,
the last line of the log of a frame that is not done) and prints one line
per frame, and gzip + base64 keep the output to a few tens of kB for a few
thousand frames. On a Slurm facility the same command lists this user's
queued and running jobs (squeue), so a `.running` claim whose job is gone
reads as "stale". Nothing is written on the cluster.
"""
import base64
import datetime
import gzip
import posixpath
import shlex
import statistics

from hpc_agent_core import config
from hpc_agent_core.middleware import quote_path, run_command
from hpc_codes import records

#: the real fleet's directory conventions (codes/common/campaign/scripts/run_fleet.sh)
LAYOUT = {
    "group_glob": "[A-Z]*",          # one folder per group (composition, system) at the root
    "frame_glob": "f[0-9][0-9][0-9]",  # one folder per frame inside a group
    "done_dir": "completed",         # state folders, relative to the root, each holding <group>/<frame>
    "failed_dir": "failed",
    "held_dirs": ["scf_hold"],       # parked by hand; the fleet ignores them
    "claim_dir": ".running",         # mkdir'd by the lane that runs the frame
    "claim_job_file": "jobid",       # inside claim_dir: the claiming job's id
    "attempts_file": ".attempts",    # tries so far (an integer)
    "lastjob_file": ".lastjob",      # the job that tried last; its mtime is the try's start
    "reason_file": "REASON",         # why the fleet retired the frame to failed/
    "flag_files": ["NEEDS_POTCAR"],  # markers that a pending frame cannot start
    "log_file": "vasp.out",          # its last line is shown for frames that are not done
    "scf_file": "OSZICAR",           # nscf = its lines matching scf_regex
    "scf_regex": "^(DAV|RMM|CG):",
    "scf_limit_file": "INCAR",       # nelm = the value of scf_limit_key in it
    "scf_limit_key": "NELM",
    "end_file": "OUTCAR",            # wall_s = its mtime - lastjob_file's mtime
}
STATES = ("pending", "running", "stale", "done", "failed", "held")
#: attention kinds, most urgent first
KINDS = ("stale", "failed", "flagged", "retried", "held")
_LIST_KEYS = ("held_dirs", "flag_files")
_FILE_KEYS = ("claim_dir", "claim_job_file", "attempts_file", "lastjob_file", "reason_file",
              "log_file", "scf_file", "scf_limit_file", "end_file")
_SCF_MAX = 4_000_000        # bytes: a larger scf_file is not read (no nscf)
_LOG_MAX = 2_000_000        # bytes: a larger log is not read (no log_tail)
_TEXT_MAX = 200             # characters kept of a reason or a log line

_AWK = r'''
function clean(s) { gsub(/[\t\r]/, " ", s); return substr(s, 1, textmax) }
function first(f,   l, r) { r = ""; if ((getline l < f) > 0) r = l; close(f); return clean(r) }
function count(f,   l, c) { c = 0; while ((getline l < f) > 0) if (l ~ scfre) c++; close(f); return c }
function keyval(f,   l, u, v) {
    v = ""
    while ((getline l < f) > 0) {
        u = toupper(l)
        if (v == "" && u ~ ("^[ \t]*" limkey "[ \t]*=")) {
            sub(/^[^=]*=[ \t]*/, "", u); sub(/[^0-9].*$/, "", u); v = u
        }
    }
    close(f); return v
}
function lastline(f,   l, r) {
    r = ""; while ((getline l < f) > 0) if (l ~ /[^ \t\r]/) r = l; close(f); return clean(r)
}
BEGIN { FS = "\t"; OFS = "\t"; nflag = split(flags, fa, "/"); for (i = 1; i <= nflag; i++) isflag[fa[i]] = 1 }
$1 == "D" { n++; order[n] = $2; known[$2] = 1; next }
$1 == "F" {
    mt = int($2); sz = $3 + 0; p = $4
    i = length(p); while (i > 0 && substr(p, i, 1) != "/") i--
    fr = substr(p, 1, i - 1); nm = substr(p, i + 1)
    if (!(fr in known)) next
    if (nm == attf) att[fr] = first(p)
    else if (nm == lastf) { lj[fr] = first(p); ljt[fr] = mt }
    else if (nm == claimd) { cl[fr] = 1; cj[fr] = first(p "/" claimj); ct[fr] = mt }
    else if (nm == reasf) rs[fr] = first(p)
    else if (nm == scff) { if (sz <= scfmax) ns[fr] = count(p) }
    else if (nm == limf) ne[fr] = keyval(p)
    else if (nm == endf) et[fr] = mt
    else if (nm == logf) {
        if (substr(fr, 1, length(donep)) != donep) lt[fr] = (sz <= logmax) ? lastline(p) : "(log over " logmax " bytes, not read)"
    }
    else if (nm in isflag) fl[fr] = fl[fr] (fl[fr] == "" ? "" : "/") nm
    next
}
END {
    for (k = 1; k <= n; k++) {
        fr = order[k]
        print "R", fr, att[fr], lj[fr], ljt[fr], ((fr in cl) ? 1 : 0), cj[fr], ct[fr], ns[fr], ne[fr], et[fr], fl[fr], rs[fr], lt[fr]
    }
}
'''


def layout_of(layout: dict | None) -> dict:
    """LAYOUT with the caller's overrides; refuses an unknown key."""
    lay = dict(LAYOUT)
    for k, v in (layout or {}).items():
        if k not in LAYOUT:
            raise ValueError(f"unknown layout key {k!r}; valid keys: {', '.join(LAYOUT)}")
        if k in _LIST_KEYS:
            v = [v] if isinstance(v, str) else list(v or [])
        lay[k] = v
    for k in ("group_glob", "frame_glob", "done_dir", "failed_dir"):
        if not lay[k]:
            raise ValueError(f"layout {k} cannot be empty")
    for k in _FILE_KEYS:
        if not isinstance(lay[k], (str, type(None))):
            raise ValueError(f"layout {k} must be a file name or null")
    for name in [lay[k] for k in _FILE_KEYS] + lay["flag_files"]:
        if name and ("/" in name or "\t" in name or "\n" in name):
            raise ValueError(f"layout file name {name!r}: a plain name inside the frame folder")
    return lay


def _state_dirs(lay: dict) -> list[tuple[str, str]]:
    """(folder, state), longest folder first so a nested one wins."""
    pairs = [(lay["done_dir"], "done"), (lay["failed_dir"], "failed")]
    pairs += [(h, "held") for h in lay["held_dirs"] if h]
    return sorted(((d.strip("/"), s) for d, s in pairs), key=lambda p: -len(p[0]))


def command(root: str, lay: dict, queue: bool, max_frames: int) -> str:
    """The one remote command: read-only, output gzip + base64."""
    q = shlex.quote
    dirs = [d for d, _ in _state_dirs(lay)]
    markers = [lay[k] for k in ("attempts_file", "lastjob_file", "claim_dir", "reason_file",
                                "scf_file", "scf_limit_file", "end_file", "log_file") if lay[k]]
    markers += [f for f in lay["flag_files"] if f]
    names = " -o ".join(f"-name {q(m)}" for m in dict.fromkeys(markers))
    awk_vars = {"attf": lay["attempts_file"], "lastf": lay["lastjob_file"], "claimd": lay["claim_dir"],
                "claimj": lay["claim_job_file"], "reasf": lay["reason_file"], "scff": lay["scf_file"],
                "scfre": lay["scf_regex"], "limf": lay["scf_limit_file"], "limkey": (lay["scf_limit_key"] or "").upper(),
                "endf": lay["end_file"], "logf": lay["log_file"], "flags": "/".join(f for f in lay["flag_files"] if f),
                "donep": "./" + lay["done_dir"].strip("/") + "/", "scfmax": _SCF_MAX, "logmax": _LOG_MAX,
                "textmax": _TEXT_MAX}
    # awk -v would turn backslashes in the regex into escapes; ENVIRON keeps them
    env = " ".join(f"HPC_CAMPAIGN_{k}={q(str(v or ''))}" for k, v in awk_vars.items())
    awk_begin = "BEGIN { " + "; ".join(f'{k} = ENVIRON["HPC_CAMPAIGN_{k}"]' for k in awk_vars) + " }\n"
    queue_part = ('if command -v squeue >/dev/null 2>&1 && Q=$(squeue -h -u "$USER" -o %A 2>/dev/null); then '
                  'echo QOK; for j in $Q; do echo "Q $j"; done; fi; ') if queue else ""
    return (
        f"cd {quote_path(root)} 2>/dev/null || {{ echo HPC_CAMPAIGN_NOROOT; exit 0; }}; "
        "shopt -s nullglob; "
        f"G={q(lay['group_glob'])}; F={q(lay['frame_glob'])}; SD=({' '.join(q(d) for d in dirs)}); "
        "frames=(); "
        "skip() { local s; for s in \"${SD[@]}\"; do [ \"$1\" = \"$s\" ] && return 0; done; return 1; }; "
        "IFS=; for d in $G/$F; do [ -d \"$d\" ] || continue; skip \"${d%%/*}\" && continue; frames+=(\"./$d\"); done; "
        "for s in \"${SD[@]}\"; do for d in \"$s\"/$G/$F; do [ -d \"$d\" ] && frames+=(\"./$d\"); done; done; "
        "unset IFS; "
        f"if [ ${{#frames[@]}} -gt {int(max_frames)} ]; then echo \"HPC_CAMPAIGN_TOOMANY ${{#frames[@]}}\"; exit 0; fi; "
        "{ echo \"NOW $(date +%s)\"; " + queue_part +
        "if [ ${#frames[@]} -gt 0 ]; then "
        "{ printf 'D\\t%s\\n' \"${frames[@]}\"; "
        f"find \"${{frames[@]}}\" -mindepth 1 -maxdepth 1 \\( {names} \\) -printf 'F\\t%T@\\t%s\\t%p\\n' 2>/dev/null; }} "
        f"| {env} awk {q(awk_begin + _AWK)}; fi; }} | gzip -c | base64"
    )


def _decode(out: str) -> list[str]:
    text = out.strip()
    if text.startswith("HPC_CAMPAIGN_"):
        return text.splitlines()
    if "[output truncated" in text:
        raise RuntimeError("campaign_status: the cluster's answer was truncated; narrow the layout "
                           "(group_glob) and call again")
    return gzip.decompress(base64.b64decode(text)).decode(errors="replace").splitlines()


def _int(s: str):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _frame(path: str, f: list[str], lay: dict, now: int, queue: set | None, many: int) -> dict:
    state, fid = None, path
    for d, s in _state_dirs(lay):
        if path.startswith(d + "/"):
            state, fid = s, path[len(d) + 1:]
            break
    att, lastjob, lj_t, claimed, claim_job, claim_t, nscf, nelm, end_t, flags, reason, log_tail = f
    claim_job = claim_job or None
    if state is None:
        if claimed == "1":
            state = "stale" if queue is not None and claim_job not in queue else "running"
        else:
            state = "pending"
    lj_t, end_t = _int(lj_t), _int(end_t)
    rec = {"frame": fid, "group": fid.split("/", 1)[0], "path": path, "state": state,
           "attempts": _int(att), "job_id": lastjob or None, "nscf": _int(nscf), "nelm": _int(nelm),
           "wall_s": (end_t - lj_t) if lj_t is not None and end_t is not None and end_t >= lj_t else None,
           "flags": [x for x in flags.split("/") if x], "reason": reason or None,
           "log_tail": log_tail or None}
    if claimed == "1":
        rec["claim_job"] = claim_job
        rec["claimed_s_ago"] = now - _int(claim_t) if _int(claim_t) is not None else None
    why = []
    if state == "stale":
        why.append(f"claimed by job {claim_job or '(unknown)'}, which is no longer in the queue")
    if state == "failed":
        why.append(reason or "in the failed folder, no reason file")
    if state == "held":
        why.append("held by hand")
    if rec["flags"] and state != "done":
        why.append("flagged " + ", ".join(rec["flags"]))
    if state != "done" and (rec["attempts"] or 0) >= many:
        why.append(f"{rec['attempts']} attempts")
    if state != "done" and rec["nscf"] is not None and rec["nelm"] and rec["nscf"] >= rec["nelm"]:
        why.append(f"SCF hit its limit ({rec['nscf']}/{rec['nelm']} steps)")
    if why:
        kind = ("stale" if state == "stale" else "failed" if state == "failed"
                else "flagged" if rec["flags"] and state != "held" else "held" if state == "held" else "retried")
        rec["kind"], rec["why"] = kind, why
    return rec


def status(facility: str, root: str, layout: dict | None = None, many_attempts: int = 2,
           limit: int = 40, record: bool = False, campaign: str | None = None,
           max_frames: int = 20000) -> dict:
    lay = layout_of(layout)
    config.require_facility_configuration(facility)
    if not root or not root.strip():
        raise ValueError("give the campaign root folder on the cluster, e.g. ~/agent/work/<campaign>")
    try:
        from facilities.registry import get_backend
        from hpc_agent_core.compute.slurm import SlurmBackend
        queue_known = isinstance(get_backend(facility), SlurmBackend)
    except ValueError:
        queue_known = False
    lines = _decode(run_command(facility, command(root, lay, queue_known, max_frames), timeout=300))
    if lines and lines[0] == "HPC_CAMPAIGN_NOROOT":
        raise ValueError(f"no folder {root} on {facility}")
    if lines and lines[0].startswith("HPC_CAMPAIGN_TOOMANY"):
        raise ValueError(f"{lines[0].split()[1]} frame folders under {root}, more than max_frames="
                         f"{max_frames}; raise max_frames or narrow group_glob")
    now, queue, rows = None, None, []
    for line in lines:
        if line.startswith("NOW "):
            now = int(line.split()[1])
        elif line == "QOK":
            queue = set()
        elif line.startswith("Q ") and queue is not None:
            queue.add(line[2:].strip())
        elif line.startswith("R\t"):
            parts = line.split("\t")
            parts += [""] * (14 - len(parts))
            path = parts[1][2:] if parts[1].startswith("./") else parts[1]
            rows.append((path, parts[2:14]))
    frames = [_frame(p, f, lay, now or 0, queue, many_attempts) for p, f in rows]

    warnings = []
    seen = {}
    for fr in frames:
        if fr["frame"] in seen:
            warnings.append(f"{fr['frame']} is in two places: {seen[fr['frame']]} and {fr['path']}")
        seen.setdefault(fr["frame"], fr["path"])
    if not frames:
        warnings.append(f"no frame folders match {lay['group_glob']}/{lay['frame_glob']} under {root} "
                        "or its state folders; check the layout")
    if queue is None and any(fr["state"] == "running" for fr in frames):
        warnings.append("the queue was not read (not a Slurm facility, or squeue failed): "
                        "a claim whose job has ended reads as running")

    counts = {s: 0 for s in STATES}
    by_group = {}
    walls = {}
    for fr in frames:
        counts[fr["state"]] += 1
        g = by_group.setdefault(fr["group"], {s: 0 for s in STATES})
        g[fr["state"]] += 1
        if fr["state"] == "done" and fr["wall_s"] is not None:
            walls.setdefault(fr["group"], []).append(fr["wall_s"])
    for g, ws in walls.items():
        by_group[g]["done_wall_s_median"] = statistics.median(ws)

    keep = ("frame", "state", "attempts", "job_id", "nscf", "nelm", "wall_s", "flags", "reason", "log_tail")
    need = sorted((fr for fr in frames if "kind" in fr),
                  key=lambda fr: (KINDS.index(fr["kind"]), fr["frame"]))
    attention = [{**{k: fr[k] for k in keep if fr[k] not in (None, [])}, "why": fr["why"]}
                 for fr in need[:limit]]
    running = [{k: fr[k] for k in ("frame", "claim_job", "claimed_s_ago", "attempts", "nscf")}
               for fr in frames if fr["state"] == "running"][:limit]

    taken = datetime.datetime.fromtimestamp(now or 0, datetime.timezone.utc) if now else \
        datetime.datetime.now(datetime.timezone.utc)
    taken_at = taken.strftime("%Y-%m-%dT%H:%M:%SZ")
    left = len(frames) - counts["done"]
    out = {
        "root": root, "taken_at": taken_at, "total": len(frames), "left": left,
        "counts": counts, "by_group": dict(sorted(by_group.items())),
        "summary": f"{counts['done']} of {len(frames)} done; {left} left: " + ", ".join(
            f"{counts[s]} {s}" for s in STATES if s != "done" and counts[s]),
        "attention_total": len(need),
        "attention_by_kind": {k: sum(1 for fr in need if fr["kind"] == k) for k in KINDS
                              if any(fr["kind"] == k for fr in need)},
        "attention": attention, "running": running,
        "queue_read": queue is not None, "warnings": warnings,
    }
    if record:
        rec = snapshot(facility, root, campaign, taken, lay, frames, counts, by_group)
        out["record_id"] = records.append(facility, "campaign", rec)["id"]
    return out


def snapshot(facility, root, campaign, taken, lay, frames, counts, by_group) -> dict:
    """A `campaign` record (hpc_codes/schemas/records.yaml) of this status."""
    name = campaign or posixpath.basename(root.rstrip("/")) or "campaign"
    fields = ("state", "attempts", "job_id", "nscf", "nelm", "wall_s", "flags", "reason", "log_tail")
    return {
        "id": f"{name}-{taken.strftime('%Y%m%dT%H%M%S')}",
        "facility": facility, "campaign": name, "root": root,
        "taken_at": taken.strftime("%Y-%m-%dT%H:%M:%SZ"), "date": taken.strftime("%Y-%m-%d"),
        "layout": {k: v for k, v in lay.items() if v != LAYOUT[k]},
        "total": len(frames), "counts": counts,
        "by_group": {g: {s: c[s] for s in STATES} for g, c in sorted(by_group.items())},
        "frames": {fr["frame"]: {k: fr[k] for k in fields if fr[k] not in (None, [])}
                   for fr in sorted(frames, key=lambda fr: fr["frame"])},
    }
