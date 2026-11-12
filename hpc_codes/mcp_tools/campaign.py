"""hpc_codes MCP tools: campaign.

hpc_codes/server.py loads every module in this folder. A new campaign tool goes here;
a new group of tools is a new module beside this one.

Many-frame campaigns run as a restartable "fleet" job (G33). campaign_status
reads the campaign's folder tree on the login node in one command and changes
nothing there, so, unlike run_command_on_cluster, it needs no
preview-and-permission step.
"""
from hpc_codes import campaign
from hpc_codes.mcp_tools import _tool


@_tool
def campaign_status(facility: str, root: str, layout: dict | None = None,
                    many_attempts: int = 2, limit: int = 40, record: bool = False,
                    campaign_name: str | None = None, max_frames: int = 20000) -> dict:
    """Where a many-frame "fleet" campaign stands: every frame's state,
    rebuilt from its folder tree on the cluster in ONE read-only command
    (a bash glob, one find, one awk; output gzipped, a few tens of kB for
    thousands of frames). No permission step needed.

    The tree is the fleet driver's (the campaign skill's run_fleet.sh):
    <root>/<group>/<frame>/ is pending, or running while a lane's
    `.running/` claim folder is in it; completed/, failed/ and the
    hand-made scf_hold/ hold retired frames as <state dir>/<group>/<frame>/.
    Per frame it reads `.attempts`, `.lastjob`, `.running/jobid`, `REASON`,
    flag files (NEEDS_POTCAR), the SCF step count (OSZICAR lines) and limit
    (INCAR NELM), the end file's time (OUTCAR) and, for a frame that is not
    done, the last line of its log (vasp.out). On a Slurm facility the
    same command lists the user's jobs, so a claim whose job has left the
    queue reads as `stale` (the fleet's next run reaps it).

    States: pending, running, stale, done, failed, held. A frame needs
    attention when it is stale, failed, flagged, held, or not done after
    `many_attempts` tries; `why` says which, with the reason file's line,
    "SCF hit its limit (n/NELM steps)" and the log's last line.

    Returns {"root", "taken_at", "total", "left", "counts": {state: n},
    "by_group": {group: {state: n, done_wall_s_median}}, "summary",
    "attention_total", "attention_by_kind", "attention": [first `limit`,
    most urgent first: {frame, state, attempts, job_id, nscf, nelm,
    wall_s, flags, reason, log_tail, why}], "running": [{frame,
    claim_job, claimed_s_ago, attempts, nscf}], "queue_read", "warnings"},
    plus "record_id" when record=true. wall_s is the last try's run time
    from file times (end file's mtime - .lastjob's mtime).

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        root: the campaign folder on the cluster, absolute or under ~.
        layout: overrides of the folder and marker names, any of
            group_glob ("[A-Z]*"), frame_glob ("f[0-9][0-9][0-9]"),
            done_dir ("completed"), failed_dir ("failed"), held_dirs
            (["scf_hold"]), claim_dir (".running"), claim_job_file
            ("jobid"), attempts_file (".attempts"), lastjob_file
            (".lastjob"), reason_file ("REASON"), flag_files
            (["NEEDS_POTCAR"]), log_file ("vasp.out"), scf_file
            ("OSZICAR"), scf_regex ("^(DAV|RMM|CG):"), scf_limit_file
            ("INCAR"), scf_limit_key ("NELM"), end_file ("OUTCAR"). A
            null file name skips that reading.
        many_attempts: tries after which a frame not done needs attention.
        limit: at most this many attention and running entries.
        record: also append this snapshot as a `campaign` record (every
            frame's state, attempts, nscf, nelm, wall_s, reason) to the
            user's records; returns its id. Do it when the state matters
            later (before changing the method, at a milestone), not on
            every check: a snapshot holds every frame.
        campaign_name: the record's campaign label; default the root
            folder's name.
        max_frames: refuse a tree with more frame folders than this.
    """
    return campaign.status(facility, root, layout, many_attempts, limit, record,
                           campaign_name, max_frames)
