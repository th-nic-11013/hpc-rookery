"""The unified MCP tool surface — one copy of every tool, shared by every
registered facility.

Every tool takes an explicit `facility` argument (the slug from
get_facilities(), e.g. "rikyu") as its first parameter and dispatches to
that facility's registered SchedulerBackend (facilities.registry) and
config.Facility (hpc_agent_core.config) — there is no per-facility copy of
this file. An unknown facility slug raises a ValueError listing every valid
slug (see config.get_facility / facilities.registry.get_backend), so a
wrong or guessed name fails loudly rather than silently reaching the wrong
cluster.

Tool groups mirror the IRI Facility API resource groups (facility, status,
account, compute, filesystem); see IRI_CHECKLIST.md at the repo root for
coverage. The docs-search tools (hpc_agent_core.docs_server) and the
code-pack tools (hpc_codes.server) are registered on this same server at the
bottom of this file, each in its own guard. Tools are thin verbs — workflow knowledge (when to pick which
facility, how to read a failed job) lives in plugins/hpc/skills/, not in
long docstrings here.
"""
import posixpath
import shlex
import sys
import json
import time
from functools import wraps
from typing import Annotated, Literal
from pathlib import Path

import hpc_mcp  # noqa: F401 -- import for its side effect: registers every facility
from hpc_agent_core.mcp_server import MCPServer

from hpc_agent_core import config, middleware, submit_guard
from hpc_agent_core.middleware import (
    download_file,
    quote_path,
    run_command,
    upload_file,
)
from hpc_agent_core.models import CompressionType, Job, JobSpec, JobState
from pydantic import BeforeValidator, Field
from hpc_agent_core.serving import serve
from facilities.registry import get_backend

#: The server's MCP `instructions`, sent once at initialize: routing only,
#: at most 5 lines (a small worker treats every line as a step to take; see
#: notes/skills-mcp-harmony.md). A plain string constant so other repos
#: (kairo's bench stub) can read it from this file without importing it.
SERVER_INSTRUCTIONS = """\
Every tool takes `facility` (a slug) as its first argument; there is no default facility.
If you don't know the facility slug, call get_facilities() first.
Facility how-to lives in the `<slug>-*` skills and in read_doc_section / search_docs.
Prefer a typed tool (fs_*, submit_job, get_job_status, ...) over run_command_on_cluster."""

mcp = MCPServer("hpc-mcp", instructions=SERVER_INSTRUCTIONS)

#: Seconds an fs_* tool whose run time grows with the data (tar, cp, mv
#: across filesystems, rm -r, sha256sum) lets its command run, instead of the
#: transport's ~30 s default: packing a few thousand small files on a
#: parallel filesystem takes minutes (G44). A command that outlives even
#: this gets run_command's "may still be running" message, not a failure.
LONG_FS_TIMEOUT_S = 600

_TAR_FLAGS = {
    CompressionType.NONE: "cf",
    CompressionType.GZIP: "czf",
    CompressionType.BZIP2: "cjf",
    CompressionType.XZ: "cJf",
}


# ---------------------------------------------------------------------------
# Facility / resource info
# ---------------------------------------------------------------------------

def _require_facility_configuration(facility: str) -> None:
    """Require an explicit, usable SSH destination before a tool dispatches.

    Registered default hosts describe a facility; they are not evidence that
    this user has configured access to it.  Checking here makes an absent,
    malformed, or incomplete config fail with the same actionable setup
    directions before a backend can return a misleading capability result.
    Remote operations subsequently perform their normal SSH call, which
    turns a bad host/key into the same setup guidance with the SSH detail.
    """
    config.require_facility_configuration(facility)


def _configured_facility_tool(func):
    """Decorate every facility-scoped MCP tool with the common guard."""
    @wraps(func)
    def wrapped(facility: str, *args, **kwargs):
        _require_facility_configuration(facility)
        return func(facility, *args, **kwargs)
    return wrapped

@mcp.tool()
def get_facilities() -> list[dict]:
    """Returns the list of facility slugs (with display name and description)
    this server can reach, and nothing else. Use it only when you do not
    already know the facility slug, e.g. "which machines can you reach?".
    Every other tool's `facility` argument must be one of the `slug` values
    returned here.

    It says nothing about a facility's contents. If the facility is already
    named, do not call this: for partitions and how busy they are use
    get_resources, for one partition get_resource, for down nodes
    get_drained_nodes, for a project's budget get_project_allocations, for
    static limits/storage/modules get_facility, and for documentation
    search_docs."""
    listing = [
        {"slug": f.slug, "display_name": f.display_name, "description": f.description}
        for f in config.list_facilities()
    ]
    # A facility whose module failed to import is skipped at startup so it
    # can't deny the server to everyone else — but it must not vanish
    # silently, or a user asking "why can't I see machine X" gets no answer.
    for slug, error in hpc_mcp.FAILED_FACILITIES.items():
        listing.append({
            "slug": slug,
            "display_name": f"{slug} (unavailable)",
            "description": f"This facility failed to load and cannot be used: {error}. "
                            "Report it to whoever maintains this deployment.",
            "unavailable": True,
        })
    return listing


@mcp.tool()
# Not gated: reads this facility's packaged facts JSON and opens no
# connection, so it must answer before the user has configured SSH —
# that is how an agent orients someone who has not set up yet.
def get_facility(facility: str) -> dict:
    """Returns static facts for one facility: partitions, the resource-limit
    table, storage tiers, modules, and Spack. Not live; for current node
    counts use get_resources, for prose documentation use search_docs.
    (IRI: GET /api/v1/facility)

    Args:
        facility: Facility slug, e.g. "rikyu". See get_facilities().
    """
    return config.load_facts(facility)


@mcp.tool()
@_configured_facility_tool
def get_resources(facility: str) -> list[dict]:
    """Returns the list of a facility's compute partitions ("resources" in
    IRI terms) with live allocated/idle/other/total node counts, i.e. "what
    partitions are there" and "will a job start soon". A live query, unlike
    get_facility's static data. For a single partition use get_resource; for
    down nodes use get_drained_nodes; to learn which facilities exist use
    get_facilities.
    (IRI: GET /api/v1/status/resources)
    """
    return get_backend(facility).get_live_resources()


@mcp.tool()
@_configured_facility_tool
def get_resource(facility: str, name: str) -> dict:
    """Returns live occupancy (allocated/idle/other/total nodes) for one named
    partition, e.g. name="gpu". For all partitions at once use get_resources;
    for static limits use get_facility.
    (IRI: GET /api/v1/status/resources/{resource_id})

    Args:
        facility: Facility slug, e.g. "rikyu". See get_facilities().
        name: Partition name exactly as listed by get_resources.
    """
    for p in get_backend(facility).get_live_resources():
        if p["partition"] == name:
            return p
    raise ValueError(f"No resource named {name!r} on facility {facility!r}")


@mcp.tool()
@_configured_facility_tool
def get_drained_nodes(facility: str) -> list[dict]:
    """Returns the nodes currently drained or down and the reason for each,
    i.e. "are any nodes down for maintenance" or "why won't my job start".
    For partition-level occupancy use get_resources. (Extension — not an IRI
    endpoint.)"""
    return get_backend(facility).get_drained_nodes()


@mcp.tool()
@_configured_facility_tool
def get_projects(facility: str) -> list[dict]:
    """Projects (scheduler accounts) the current user may charge on this
    facility, each with the partitions and QOS it allows.
    (IRI: GET /api/v1/account/projects)

    Each entry's `account` is what goes in a JobSpec's
    `attributes.account`. Some facilities return more than the bare
    associations — e.g. a facility with fair-share scheduling also reports
    the standing that governs when a queued job actually starts. Requires
    per-project accounting; a facility without it raises a clear error.
    """
    return get_backend(facility).get_projects()


@mcp.tool()
@_configured_facility_tool
def get_project(facility: str, account: str) -> dict:
    """Details for a single project/account.
    (IRI: GET /api/v1/account/projects/{project_id})"""
    for p in get_backend(facility).get_projects():
        if p["account"] == account:
            return p
    raise ValueError(
        f"Account {account!r} is not one the current user can charge on facility "
        f"{facility!r}. Call get_projects to see the available accounts."
    )


@mcp.tool()
@_configured_facility_tool
def get_project_allocations(facility: str, project_id: str) -> dict:
    """Returns a project's allocation limits — the account-wide ceilings shared
    by everyone under it, i.e. how much of the project's budget is allowed.
    Use get_user_allocations for the current user's own share, get_projects
    to find the project_id (account name).
    (IRI: GET /api/v1/account/projects/{project_id}/project_allocations)

    On Slurm: GrpTRESMins caps cumulative core-time, GrpTRESRunMins caps
    concurrently-running core-time. Either may be empty on a site that
    doesn't enforce that limit — that's not an error.
    """
    return get_backend(facility).get_project_allocations(project_id)


@mcp.tool()
@_configured_facility_tool
def get_user_allocations(facility: str, project_id: str) -> dict:
    """The current user's allocation share within a project — the per-user
    slice of get_project_allocations' account-wide ceiling.
    (IRI: GET /api/v1/account/projects/{project_id}/project_allocations/{id}/user_allocations)
    """
    return get_backend(facility).get_user_allocations(project_id)


# ---------------------------------------------------------------------------
# Job submit / status / cancel / update
# ---------------------------------------------------------------------------

def _spec_from_json(value):
    """Some clients send the spec as a JSON string; parse it, then let
    JobSpec validate it exactly as for an object."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as e:
            raise ValueError(f"spec is not valid JSON ({e}); pass spec as an object, not a string")
    return value


SpecArg = Annotated[JobSpec, BeforeValidator(_spec_from_json)]
#: In the schema, so a model sees it next to `spec` (G95 step 5b: refusal paths testable
#: without a submit).
DRY_RUN = ("true: run every check submit_job would (submit guard, facility validation, budget) "
           "and render the script, return the verdict, and submit nothing.")


@mcp.tool()
# Not gated: apply_defaults/validate_spec/render_script are pure local
# work for every facility (asserted in tests/conformance.py), so this
# stays useful before SSH is configured.
def render_job_script(facility: str, spec: SpecArg) -> str:
    """Render the batch script for a JobSpec *without* submitting it, with
    that facility's defaults already applied. Use this to show the user
    exactly what will run before calling submit_job (the "show before you
    run" rule) — it costs nothing and touches no scheduler.
    (Extension — no IRI counterpart.)
    """
    backend = get_backend(facility)
    backend.apply_defaults(spec)
    backend.validate_spec(spec)
    return backend.render_script(spec)


@mcp.tool()
@_configured_facility_tool
def submit_job(facility: str, spec: SpecArg, dry_run: Annotated[bool, Field(description=DRY_RUN)] = False) -> dict:
    """Submit a job to a facility's scheduler. Show the user the spec
    before submitting unless they asked to just run it — use
    render_job_script for an exact preview.

    Facility defaults are filled in first: a facility with one obvious
    partition supplies it when spec.attributes.queue_name is blank, and a
    facility that requires a project/account supplies the user's configured
    default (raising a clear, actionable error if none can be resolved).
    (IRI: POST /api/v1/compute/job/{resource_id})

    When the server runs with HPC_SUBMIT_GUARD=1 (an unattended agent
    harness), only a launch_plan job_spec is accepted: keep its
    plan_signature, resources and attributes, add name, directory,
    executable and attributes.duration. A job whose worst case
    (walltime x nodes x cores per node) is over the facility's configured
    core-hour budget is refused, with the numbers.

    dry_run=True runs every check a submit would (guard signature and
    unchanged resources/attributes, facility validation, budget) and the
    script render, and returns the verdict instead of submitting:
    {"verdict": "accept"|"refuse", "refusals": [...], "guard": "on"|"off",
    "worst_case", "budget_core_hours", "script", "submitted": false}.
    Nothing reaches the scheduler.

    Args:
        facility: Facility slug, e.g. "rikyu". See get_facilities().
        spec: The job to submit.
        dry_run: Check and render only; never submit.
    """
    backend = get_backend(facility)
    if dry_run:
        return submit_guard.dry_run(facility, spec, backend)
    guarded = submit_guard.enabled()
    if guarded:
        submit_guard.check_signature(facility, spec)
    backend.apply_defaults(spec)
    backend.validate_spec(spec)
    if guarded:
        submit_guard.check_budget(facility, spec)
    return backend.submit(spec)


@mcp.tool()
@_configured_facility_tool
def get_job_status(facility: str, job_id: str) -> Job:
    """Status of a single job. (IRI: GET /api/v1/compute/status/{resource_id}/{job_id})"""
    jobs = get_backend(facility).get_statuses([job_id])
    if not jobs:
        raise ValueError(f"Job {job_id} not found on facility {facility!r}")
    return jobs[0]


@mcp.tool()
@_configured_facility_tool
def get_job_statuses(facility: str, job_ids: list[str]) -> list[Job]:
    """Status of several jobs, or — if job_ids is empty — every job the
    current user has touched in roughly the last two days (on a facility
    with Slurm accounting; otherwise just the live queue).
    (IRI: POST /api/v1/compute/status/{resource_id})"""
    backend = get_backend(facility)
    return backend.get_statuses(job_ids) if job_ids else backend.get_recent_statuses()


#: wait_for_jobs bounds: one call blocks at most WAIT_MAX_S, and polls the
#: scheduler no more often than WAIT_MIN_POLL_S (shared login nodes).
WAIT_MAX_S = 3600
WAIT_MIN_POLL_S = 15
_ENDED = {JobState.COMPLETED, JobState.FAILED, JobState.CANCELED}


def _job_row(job: Job) -> dict:
    st = job.status
    meta = (st.meta_data or {}) if st else {}
    return {"id": job.id, "state": st.state.value if st else JobState.UNKNOWN.value,
            "native_state": meta.get("native_state"), "exit_code": st.exit_code if st else None,
            "elapsed": meta.get("elapsed"), "workdir": meta.get("workdir"),
            "req_mem": meta.get("req_mem"), "max_rss": meta.get("max_rss"),
            "message": st.message if st else None}


def _wait_for_jobs(backend, job_ids: list[str], timeout_s: float, poll_s: float, until: str,
                   sleep=time.sleep, clock=time.monotonic) -> dict:
    """The polling behind wait_for_jobs, with sleep and clock injectable so the
    offline checks can run it against a fake backend in no time."""
    start, polls, failures, unseen, rows = clock(), 0, 0, {i: 0 for i in job_ids}, []
    while True:
        polls += 1
        try:
            by_id = {j.id: j for j in backend.get_statuses(job_ids)}
            failures = 0
        except Exception as e:  # a dropped SSH connection mid-wait is retried, not fatal
            failures += 1
            if failures >= 3:
                raise RuntimeError(f"3 status queries in a row failed while waiting; last: {e}") from e
            by_id = None
        if by_id is not None:
            rows = [_job_row(by_id[i]) if i in by_id else {"id": i, "state": "not_found"}
                    for i in job_ids]
            for r in rows:
                unseen[r["id"]] = unseen[r["id"]] + 1 if r["state"] == "not_found" else 0
            ended = [r["id"] for r in rows if r["state"] in {s.value for s in _ENDED}]
            met = len(ended) == len(job_ids) if until == "all" else bool(ended)
            lost = [i for i, n in unseen.items() if n >= 3]
            if met or lost:
                return _wait_result(rows, met, False, clock() - start, polls, until, lost)
        if clock() - start + poll_s > timeout_s:
            return _wait_result(rows, False, True, clock() - start, polls, until, [])
        sleep(poll_s)


def _wait_result(rows, met, timed_out, waited, polls, until, lost) -> dict:
    ended_states = {s.value for s in _ENDED}
    failed = [r["id"] for r in rows if r["state"] in (JobState.FAILED.value, JobState.CANCELED.value)]
    waiting = [r["id"] for r in rows if r["state"] not in ended_states]
    if lost:
        nxt = (f"{', '.join(lost)} not found in 3 polls: check the ID, or get_job_statuses "
               "with job_ids=[] for your recent jobs")
    elif met:
        nxt = ("read each job's output (fs_tail in its workdir)"
               + (f"; {', '.join(failed)} failed or was cancelled: read why before any resubmit"
                  if failed else ""))
    else:
        nxt = f"not ended yet: call wait_for_jobs again with job_ids={waiting}"
    return {"done": met, "timed_out": timed_out, "until": until, "waited_s": round(waited),
            "polls": polls, "jobs": rows, "failed": failed, "still_waiting": waiting,
            "not_found": lost, "next": nxt}


@mcp.tool()
@_configured_facility_tool
def wait_for_jobs(facility: str, job_ids: list[str], timeout_s: int = 600, poll_s: int = 60,
                  until: Literal["all", "any"] = "all") -> dict:
    """Wait for jobs to end (completed, failed or cancelled), checking the
    scheduler every poll_s seconds, and return when they have ended or after
    timeout_s, whichever comes first. Use it instead of a hand-written
    sleep/sacct loop or repeated get_job_status calls. (Extension — no IRI
    counterpart.)

    Returns {"done" (the jobs ended as `until` asks), "timed_out",
    "waited_s", "polls", "jobs": [{id, state, native_state, exit_code,
    elapsed, workdir, req_mem, max_rss, message}], "failed", "still_waiting", "not_found",
    "next"}. Not done after timeout_s: call it again with still_waiting.
    A job ID the scheduler doesn't know for 3 polls ends the wait.

    Args:
        facility: Facility slug, e.g. "hokusai". See get_facilities().
        job_ids: the jobs to wait for (at least one).
        timeout_s: longest this call waits, 1 to 3600 s. The MCP client's
            own tool timeout must be longer (Codex: tool_timeout_sec).
        poll_s: seconds between checks, at least 15 (shared login nodes).
        until: "all" (default) waits for every job; "any" returns as soon
            as one ends.
    """
    ids = list(dict.fromkeys(str(i).strip() for i in job_ids if str(i).strip()))
    if not ids:
        raise ValueError("job_ids needs at least one job ID")
    if not 0 < timeout_s <= WAIT_MAX_S:
        raise ValueError(f"timeout_s is 1 to {WAIT_MAX_S}; for a longer wait call again")
    if not WAIT_MIN_POLL_S <= poll_s:
        raise ValueError(f"poll_s is at least {WAIT_MIN_POLL_S} s: login nodes are shared")
    if until not in ("all", "any"):
        raise ValueError("until is 'all' or 'any'")
    return _wait_for_jobs(get_backend(facility), ids, timeout_s, min(poll_s, timeout_s), until)


@mcp.tool()
@_configured_facility_tool
def cancel_job(facility: str, job_id: str) -> Job | str:
    """Cancel a queued or running job. Confirm with the user before
    cancelling. (IRI: DELETE /api/v1/compute/cancel/{resource_id}/{job_id})"""
    return get_backend(facility).cancel(job_id)


@mcp.tool()
@_configured_facility_tool
def update_job(facility: str, job_id: str, spec: JobSpec) -> Job:
    """Apply a JobSpec to an already-submitted job; return its new state.
    (IRI: PUT /api/v1/compute/job/{resource_id}/{job_id})

    Only the fields you actually set are applied, and only those a
    scheduler can change after submission — typically the job name, wall
    time, partition, account, reservation and node count. Anything else you
    set is reported back in the returned status message as not applied,
    rather than silently ignored.

    Only affects jobs still queued or running, and is subject to the
    scheduler's own permission rules (some fields can only be lowered, not
    raised, by a non-admin).
    """
    return get_backend(facility).update(job_id, spec)


@mcp.tool()
@_configured_facility_tool
def run_command_on_cluster(facility: str, command: str) -> str:
    """Run an arbitrary shell command on a facility's login node.

    **Extension, and the most consequential one here.** IRI has no such
    endpoint, almost certainly on purpose: this bypasses every other tool's
    structure — submit_job's validation and defaults, fs_rm's refusal list,
    the whole typed surface. Prefer the specific tool whenever one fits,
    and reach for this only for things no tool covers (`module avail`,
    quota checks, attaching to a running job for diagnostics).

    Before calling this, sketch the proposed work, show the exact command(s)
    and what each will do, state whether they change anything, then ask for
    explicit permission and wait for the user's answer. Permission covers
    only the commands shown; preview and ask again before adding or
    materially changing one.

    Prefer several short, focused calls over one long command joined with
    many `;`, `&&`, or `||` operators. Inspect each result before continuing
    so a failed assumption cannot cascade into later steps. A short pipeline
    that performs one coherent check is fine. Each call starts a fresh login
    shell in `$HOME`, so `cd`, loaded modules, variables, and activated
    environments do not persist between calls.

    Afterward, summarize what actually ran, the important results, and any
    state that changed; do not merely paste raw output. Do not run heavy
    computation on the login node — submit a job instead.
    """
    return run_command(facility, command)


# ---------------------------------------------------------------------------
# Filesystem operations
# ---------------------------------------------------------------------------

@mcp.tool()
@_configured_facility_tool
def fs_ls(facility: str, path: str = ".", show_hidden: bool = True) -> str:
    """List a directory's contents (long form). (IRI: GET /api/v1/filesystem/ls/{resource_id})

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.
    """
    flags = "-la" if show_hidden else "-l"
    return run_command(facility, f"ls {flags} {quote_path(path)}")


@mcp.tool()
@_configured_facility_tool
def fs_stat(facility: str, path: str) -> str:
    """File/directory metadata: size, permissions, timestamps, owner. (IRI: GET /api/v1/filesystem/stat/{resource_id})

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.
    """
    return run_command(facility, f"stat {quote_path(path)}")


@mcp.tool()
@_configured_facility_tool
def fs_view(facility: str, path: str) -> str:
    """Read a whole text file's contents. (IRI: GET /api/v1/filesystem/view/{resource_id})

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.
    """
    return run_command(facility, f"cat {quote_path(path)}")


@mcp.tool()
@_configured_facility_tool
def fs_head(facility: str, path: str, lines: int = 20) -> str:
    """Read the first N lines of a file. (IRI: GET /api/v1/filesystem/head/{resource_id})

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.
    """
    return run_command(facility, f"head -n {int(lines)} {quote_path(path)}")


@mcp.tool()
@_configured_facility_tool
def fs_tail(facility: str, path: str, lines: int = 20) -> str:
    """Read the last N lines of a file — useful for checking a running
    job's stdout/stderr. (IRI: GET /api/v1/filesystem/tail/{resource_id})

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.
    """
    return run_command(facility, f"tail -n {int(lines)} {quote_path(path)}")


@mcp.tool()
@_configured_facility_tool
def fs_mkdir(facility: str, path: str) -> str:
    """Create a directory on the cluster, including parents as needed. (IRI: POST /api/v1/filesystem/mkdir/{resource_id})

    A folder on this machine needs no call: fs_download creates the local
    folder it writes into, and fs_compress the folder of its archive.
    A destination on the cluster needs none either: fs_extract creates
    dest_dir and fs_upload the folder of remote_path, parents too. Call
    fs_mkdir only when an empty folder is the goal.

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.
    """
    return run_command(facility, f"mkdir -p {quote_path(path)}")


@mcp.tool()
@_configured_facility_tool
def fs_upload(facility: str, local_path: str, remote_path: str) -> dict:
    """Upload a local file, or a local folder's contents, to a facility via
    rsync (falling back to scp), with a SHA-256 verification of the
    transfer. (IRI: POST /api/v1/filesystem/upload/{resource_id} — deviation: rsync/scp, folders)

    A file lands at remote_path, so remote_path names the file itself
    (folder + file name, e.g. ~/agent/work/run1/job.sh), never just the
    folder: a remote_path that is a folder is refused. Its folder is
    created if missing (parents too), so no fs_mkdir is needed first.
    A folder's *contents* land in the folder
    remote_path (created if needed; same-named files overwritten): it goes as
    one tar.gz, is extracted there, and every file's sha256 is checked on the
    cluster. macOS metadata (`._*`, .DS_Store), `__pycache__` and symlinks
    are not sent; the result lists them under `skipped`.

    Stage uploads under `~/agent/work/...` (the agent's visible scratch
    area), never in the home-directory root.

    Cluster paths (remote_path only, not local_path): keep a leading ~/
    exactly as written (the server resolves it to your home on the
    cluster). Never replace ~ with a guessed absolute home such as
    /home/<name>.
    """
    if Path(local_path).is_dir():
        return middleware.upload_dir(facility, Path(local_path), remote_path)
    return upload_file(facility, Path(local_path), remote_path)


@mcp.tool()
@_configured_facility_tool
def fs_download(facility: str, remote_path: str, local_path: str | None = None) -> dict:
    """Download a file from a facility via rsync (falling back to scp),
    with a SHA-256 verification of the transfer. (IRI: GET /api/v1/filesystem/download/{resource_id} — deviation: rsync/scp)

    local_path defaults to the same filename in the current working
    directory. Its folder is created if missing (parents too), so the
    download is the only call needed. A file that is already on this
    machine needs no download: read it where it is.

    Cluster paths (remote_path only, not local_path): keep a leading ~/
    exactly as written (the server resolves it to your home on the
    cluster). Never replace ~ with a guessed absolute home such as
    /home/<name>.
    """
    dest = Path(local_path) if local_path else Path.cwd() / Path(remote_path).name
    try:
        return download_file(facility, remote_path, dest)
    except RuntimeError as e:
        # A small worker asks to download a file it was told is already
        # local (bench L2-S4-06): say so, and name the next step.
        if not remote_path.startswith("~") and Path(remote_path).is_file():
            raise RuntimeError(
                f"remote file not found: {remote_path}. It is a file on this machine "
                "already, so no download is needed: pass this path as it is to the "
                "tool that reads it.") from e
        raise


#: Paths fs_rm refuses outright, after normalisation. Deleting any of these
#: is far more likely to be a mistake (an unset variable, a stray default,
#: a glob that expanded to nothing) than an intent, and the cost of being
#: wrong is unrecoverable — there is no trash on these filesystems.
_RM_REFUSED = {"", ".", "..", "/", "~", "*", "$HOME"}


@mcp.tool()
@_configured_facility_tool
def fs_rm(facility: str, path: str, recursive: bool = False) -> str:
    """Delete a file, or (with recursive=True) a directory tree, on the
    cluster. (IRI: DELETE /api/v1/filesystem/rm/{resource_id})

    **Destructive and not recoverable — these filesystems have no trash.
    Confirm the exact path with the user before calling this, every time,
    even if they asked for a deletion in general terms.**

    recursive is required to remove a directory: without it this is a plain
    `rm`, which fails on a directory rather than silently taking the tree
    with it. The IRI spec takes only a path; this extra flag is a
    deliberate deviation so "delete this file" can never become "delete
    this whole tree" through a typo.

    Refuses a handful of paths outright (the home directory itself, `/`,
    `.`, `..`, a bare glob) — deleting those is almost always an accident.
    That guard catches mistakes made *through this tool*; it is not a
    security boundary, because `run_command_on_cluster` can run `rm`
    directly. Treat the confirmation above as the real protection.

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.
    """
    normalized = middleware.norm_path(path).strip().rstrip("/")
    if normalized in _RM_REFUSED or path.strip() in _RM_REFUSED:
        raise ValueError(
            f"Refusing to delete {path!r}: that resolves to the home directory "
            "or a filesystem root. Name the specific file or subdirectory to "
            "remove."
        )
    flag = "-r " if recursive else ""
    quoted = quote_path(path)
    return run_command(
        facility,
        f"rm {flag}-- {quoted} && echo removed: {quoted}",
        timeout=LONG_FS_TIMEOUT_S,
    )


@mcp.tool()
@_configured_facility_tool
def fs_checksum(facility: str, path: str) -> str:
    """SHA-256 checksum of a remote file. (IRI: GET /api/v1/filesystem/checksum/{resource_id})

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.
    """
    return run_command(facility, f"sha256sum {quote_path(path)}", timeout=LONG_FS_TIMEOUT_S)


@mcp.tool()
@_configured_facility_tool
def fs_cp(facility: str, source: str, dest: str, recursive: bool = False) -> str:
    """Copy a file or (with recursive=True) a directory tree on the
    cluster. (IRI: POST /api/v1/filesystem/cp/{resource_id})

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.
    """
    flag = "-r " if recursive else ""
    return run_command(facility, f"cp {flag}{quote_path(source)} {quote_path(dest)}",
                       timeout=LONG_FS_TIMEOUT_S)


@mcp.tool()
@_configured_facility_tool
def fs_mv(facility: str, source: str, dest: str) -> str:
    """Move or rename a file/directory on the cluster. (IRI: POST /api/v1/filesystem/mv/{resource_id})

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.
    """
    return run_command(facility, f"mv {quote_path(source)} {quote_path(dest)}",
                       timeout=LONG_FS_TIMEOUT_S)


@mcp.tool()
@_configured_facility_tool
def fs_chmod(facility: str, path: str, mode: str) -> str:
    """Change a file/directory's permissions. (IRI: PUT /api/v1/filesystem/chmod/{resource_id})

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.

    Args:
        facility: Facility slug, e.g. "rikyu". See get_facilities().
        path: File or directory on the cluster.
        mode: Any chmod mode string, passed to chmod unchanged: octal digits
            as text (e.g. 755 or 0640) or symbolic (e.g. u+x, go-w, a=r).
            Not an integer.
    """
    return run_command(facility, f"chmod {shlex.quote(mode)} {quote_path(path)}")


@mcp.tool()
@_configured_facility_tool
def fs_chown(facility: str, path: str, owner: str) -> str:
    """Change a file/directory's owner (and optionally group, as
    "user:group"). Most users can only chown within their own group's
    permissions — the scheduler's filesystem still enforces the actual ACL.
    (IRI: PUT /api/v1/filesystem/chown/{resource_id})

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.
    """
    return run_command(facility, f"chown {shlex.quote(owner)} {quote_path(path)}")


@mcp.tool()
@_configured_facility_tool
def fs_symlink(facility: str, target: str, link_name: str) -> str:
    """Create a symbolic link at link_name pointing to target. (IRI: POST /api/v1/filesystem/symlink/{resource_id})

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.
    """
    return run_command(facility, f"ln -s {quote_path(target)} {quote_path(link_name)}")


@mcp.tool()
@_configured_facility_tool
def fs_compress(facility: str, paths: list[str], archive_path: str,
                 compression: CompressionType = CompressionType.GZIP,
                 match_pattern: str | None = None,
                 dereference: bool = False) -> str:
    """Create a tar archive on the cluster from one or more remote paths.
    (IRI: POST /api/v1/filesystem/compress/{resource_id})

    The archive's folder is created if missing (parents too). Fetch the
    archive with one fs_download.

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.

    match_pattern: archive only the files beneath `paths` that match this
        POSIX extended regex (GNU `find -regextype posix-extended -regex`).
        It must match the WHOLE path as find prints it, which starts with
        the path given, so begin it with `.*/`: `.*/(INCAR|POSCAR)` takes
        every INCAR and POSCAR; a bare `INCAR` matches nothing. If nothing
        matches, the call fails and no archive is written.
    dereference: follow symlinks (tar -h) rather than storing the links.
    """
    deref = "h" if dereference else ""
    flag = f"-{deref}{_TAR_FLAGS[compression]}"
    quoted_paths = " ".join(quote_path(p) for p in paths)
    parent = posixpath.dirname(archive_path.rstrip("/"))
    mkdir = f"mkdir -p {quote_path(parent)} && " if parent and parent != "~" else ""
    if match_pattern:
        # find's default regex syntax is emacs, where `(a|b)` is literal text,
        # and tar given an empty list writes an empty archive without a word
        # (G43). So name the syntax, and refuse an empty match before tar
        # runs; -print -quit stops at the first match. A find without
        # -regextype (not GNU) fails here too, loudly, with its own error.
        find = (f"find {quoted_paths} -regextype posix-extended "
                f"-regex {shlex.quote(match_pattern)}")
        nothing = (f"fs_compress: no file under {' '.join(paths)} matches match_pattern "
                   f"{match_pattern!r}, so no archive was written. It is a POSIX extended "
                   "regex matched against the whole path, which starts with the path "
                   "given: begin it with '.*/', e.g. '.*/(INCAR|POSCAR)'.")
        cmd = (f'm=$({find} -print -quit); rc=$?; if [ -z "$m" ]; then '
               f'[ "$rc" -ne 0 ] && echo "fs_compress: find failed (exit $rc); '
               f'match_pattern needs GNU find (-regextype posix-extended)" >&2; '
               f"echo {shlex.quote(nothing)} >&2; exit 3; fi; "
               f"{mkdir}{find} -print0 | tar {flag} {quote_path(archive_path)} --null -T -")
    else:
        cmd = f"{mkdir}tar {flag} {quote_path(archive_path)} {quoted_paths}"
    return run_command(facility, cmd, timeout=LONG_FS_TIMEOUT_S)


@mcp.tool()
@_configured_facility_tool
def fs_extract(facility: str, archive_path: str, dest_dir: str = ".") -> str:
    """Extract an archive on the cluster into dest_dir (created if missing,
    parents too, so no fs_mkdir is needed first). Compression format is
    auto-detected by tar. (IRI: POST /api/v1/filesystem/extract/{resource_id})

    dest_dir defaults to "." = your home folder on the cluster, not the
    archive's folder: to unpack next to the archive, pass that folder.

    Cluster paths: keep a leading ~/ exactly as written (the server
    resolves it to your home on the cluster). Never replace ~ with a
    guessed absolute home such as /home/<name>.
    """
    return run_command(
        facility,
        f"mkdir -p {quote_path(dest_dir)} && tar -xf {quote_path(archive_path)} -C {quote_path(dest_dir)}",
        timeout=LONG_FS_TIMEOUT_S,
    )


# ---------------------------------------------------------------------------
# Docs search and code-pack tools, served by this same process
# ---------------------------------------------------------------------------
# They used to be separate servers (hpc-docs-mcp, hpc-codes-mcp, both removed)
# so a fault in them could never stop the scheduler tools above from starting.
# Each group is registered inside its own guard to keep that promise: a group that fails to
# import or register is reported on stderr and in FAILED_TOOL_GROUPS, and the
# rest of the server starts as usual.

#: tool group -> why it failed to load; empty when every group loaded
FAILED_TOOL_GROUPS: dict[str, str] = {}


def _register_docs(target: MCPServer) -> None:
    from hpc_agent_core.docs_server import build
    build(target)


def _register_codes(target: MCPServer) -> None:
    from hpc_codes.server import register
    register(target)


for _group, _register in (("docs", _register_docs), ("codes", _register_codes)):
    try:
        _register(mcp)
    except Exception as e:  # never let one tool group deny the scheduler tools
        FAILED_TOOL_GROUPS[_group] = f"{type(e).__name__}: {e}"
        print(f"hpc-mcp: the {_group} tools failed to load and are unavailable: "
              f"{FAILED_TOOL_GROUPS[_group]}", file=sys.stderr)


def main():
    serve(mcp)


if __name__ == "__main__":
    main()
