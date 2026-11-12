"""hpc_codes MCP tools: cp2k_labels.

hpc_codes/server.py loads every module in this folder. A new tool for CP2K label
batches goes here; a new group of tools is a new module beside this one.

Packed label jobs (G51). Local only: reads the batch folder hyogo wrote and
launch_plan's result, writes job scripts into the batch folder, never the cluster.
"""
import json
from pathlib import Path

from hpc_agent_core import config
from hpc_codes.cp2k import label_array
from hpc_codes.mcp_tools import _tool


@_tool
def cp2k_pack_jobs(facility: str, batch_dir: str, remote_dir: str, plan: dict,
                   frames_per_job: int | None = None, max_hours: float = 24.0,
                   safety: float = 1.5, frame_s: float | None = None,
                   frames: list[str] | None = None, name: str | None = None,
                   overwrite: bool = False) -> dict:
    """Pack a CP2K label batch into jobs: K frames per job, run one after
    another by a restart-safe script (pack-NN.sh, written into the batch
    folder), and one submit_job spec per job. Local only: writes the
    scripts and packs.json, never touches the cluster.

    Before it: hyogo's cp2k_make_label_batch (the batch folder and its
    batch.json), then launch_plan(facility, "cp2k", "single_point",
    {"atoms": N, "label": ...}, method) for one frame; pass its whole
    result as plan (layout, launcher, environment, seconds per frame).

    After it: fs_upload batch_dir to remote_dir (the scripts go with the
    inputs), cp2k_check_input(facility, <remote_dir>/<check_input>), then
    for each job render_job_script(facility, spec), show it, and
    submit_job(facility, spec). Each spec runs `bash pack-NN.sh` in
    remote_dir; the launcher (srun with the binding) is inside the script.

    K: the most frames whose budget (seconds per frame x safety) fits a
    max_hours job with a margin, then balanced so the jobs are about equal;
    frames_per_job fixes K (refused when it does not fit). Fewer frames per
    job means more jobs running at once. The script starts no frame
    without its budget left, skips frames whose label.out has PROGRAM
    ENDED, keeps an unfinished one as label.out.prev and runs it again, and
    deletes each frame's wavefunction (kept for the next frame only with
    the batch's guess="previous"). Resubmitting a pack runs only what is
    left.

    For a rerun of some frames (cp2k_collect_labels' resubmit.rerun), pass
    frames: the new scripts are numbered after the existing ones and
    packs.json keeps every pack (frame -> pack -> job script).

    Returns {"jobs", "frames_per_job", "frame_s", "budget_s", "duration",
    "core_hours": {expected, requested}, "guess", "packs": [{index,
    script, frames, spec}], "upload": {local_path, remote_path},
    "check_input", "download": {fs_compress, fs_download} (after the jobs:
    one archive of the label.out files and pack logs), "warnings"}.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        batch_dir: the local batch folder (holds batch.json).
        remote_dir: where the batch will live on the cluster, under
            ~/agent/work/ (e.g. ~/agent/work/cp2k-runs/<batch>).
        plan: launch_plan's result for one frame of this batch.
        frames_per_job: K; default the most that fit max_hours.
        max_hours: the longest job to request (HOKUSAI mpc: 24).
        safety: budget factor on the seconds per frame (default 1.5).
        frame_s: seconds per frame, instead of launch_plan's estimate
            (e.g. a collector's wall_s.max from an earlier round).
        frames: frame folder names to pack (default every frame).
        name: job-name prefix (default the batch folder's name).
        overwrite: replace earlier packs (pack-*.sh, packs.json) instead of
            numbering after them.
    """
    config.get_facility(facility)          # unknown slug -> clear error; no configuration needed
    root = Path(batch_dir).expanduser()
    mp = root / "batch.json"
    if not mp.is_file():
        raise ValueError(f"{mp} is missing: batch_dir is the folder cp2k_make_label_batch wrote")
    batch = label_array.check_batch(json.loads(mp.read_text()))
    pj = root / label_array.PACKS
    old = json.loads(pj.read_text()) if pj.is_file() and not overwrite else {"packs": []}
    if overwrite:
        for s in root.glob("pack-*.sh"):
            s.unlink()
    used = [int(m.group(1)) for s in root.glob("pack-*.sh")
            if (m := label_array.PACK_RE.fullmatch(s.name))]
    first = max(used, default=0) + 1
    jobname = name or root.name
    res = label_array.pack(batch, plan, remote_dir.rstrip("/"), jobname, frames, frames_per_job,
                           max_hours, safety, frame_s, first)
    for p in res["packs"]:
        (root / p["script"]).write_text(p.pop("text"))
    old["packs"] += [{k: p[k] for k in ("index", "script", "frames")} |
                     {"duration": res["duration"], "remote_dir": remote_dir} for p in res["packs"]]
    pj.write_text(json.dumps(old, indent=1) + "\n")
    rd = remote_dir.rstrip("/")
    res["upload"] = {"local_path": str(root), "remote_path": rd}
    res["check_input"] = f"{rd}/{batch['frames'][0]['name']}/{batch['input']}"
    res["download"] = {
        "fs_compress": {"paths": [rd], "archive_path": f"{rd}/outs.tar.gz",
                        "match_pattern": r".*/f[0-9]+/label\.out\|.*/pack-[0-9]+\.log"},
        "fs_download": {"remote_path": f"{rd}/outs.tar.gz",
                        "local_path": str(root / "outs.tar.gz")},
        "then": "cp2k_collect_labels(batch_dir, archive_path=<that local_path>)"}
    return res
