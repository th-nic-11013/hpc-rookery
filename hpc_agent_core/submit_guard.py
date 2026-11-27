"""The submit guard (G95): submit_job takes only a launch_plan job spec, within budget.

Off by default. A harness that runs an agent unattended turns it on in the
MCP server's environment (``HPC_SUBMIT_GUARD=1`` in the server entry), so
interactive work and hand-written specs (TMMC, GCMC, run.sh wrappers) are
unaffected. With it on:

- launch_plan signs its job_spec: ``job_spec["plan_signature"]`` carries
  the plan's resources and attributes with an HMAC over them. The key lives
  in ``~/.hpc-agent/submit_guard.key`` (created on first use, mode 0600),
  so there is no server state and a signature survives a server restart.
- submit_job refuses a spec with no valid signature, or whose resources or
  attributes differ from the signed ones. The agent may add name,
  directory, executable (and the other script fields), attributes.duration,
  attributes.account and custom_attributes.signal; nothing else.
- submit_job refuses a job whose worst case (walltime x nodes x cores per
  node) is over the facility's core-hour budget, naming the numbers. The
  budget is ``submit_guard.budget_core_hours`` in ~/.hpc-agent/<slug>.json:
  a number, or a mapping from account to number with "*" for the rest.
  No budget configured means no budget check.
- submit_job(dry_run=True) runs the same checks and the script render and
  returns the verdict (every refusal, with the message a real submit would
  raise first) and the would-be script, submitting nothing (``dry_run``).

This guards against an agent inventing a job, not against someone who can
read the key file. It moves kairo's submit guard (kairo/agent.py) and
worst-case budget (kairo/gates.py) into the tools, so every harness gets
the same rule and needs only name-level permission rules.

Read at call time, never at import, so the server always starts.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path

from hpc_agent_core import config
from hpc_agent_core.models import JobAttributes, JobSpec, ResourceSpec

ENV = "HPC_SUBMIT_GUARD"
KEY_PATH = "~/.hpc-agent/submit_guard.key"
PREFIX = "g95.v1"

#: attributes the agent may set itself; every other attribute is the plan's.
FREE_ATTRIBUTES = {"duration", "account"}
#: custom_attributes keys the agent may add (rendered as scheduler flags, so
#: anything else could change the resources, e.g. {"nodes": "64"}).
FREE_CUSTOM = {"signal"}

REFUSED = "submit_job refused by the submit guard (G95, HPC_SUBMIT_GUARD is on): "


def enabled() -> bool:
    return os.environ.get(ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _key() -> bytes:
    path = Path(KEY_PATH).expanduser()
    try:
        key = path.read_bytes()
        if len(key) >= 32:
            return key
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def _normal(resources, attributes) -> dict:
    """The signed part of a spec: every resource field and every attribute
    the agent may not set, with defaults filled, so {} and a field at its
    default compare equal."""
    res = resources if isinstance(resources, ResourceSpec) else ResourceSpec.model_validate(resources or {})
    att = attributes if isinstance(attributes, JobAttributes) else JobAttributes.model_validate(attributes or {})
    a = att.model_dump(mode="json", exclude=FREE_ATTRIBUTES)
    a["custom_attributes"] = {k: v for k, v in a["custom_attributes"].items() if k not in FREE_CUSTOM}
    return {"resources": res.model_dump(mode="json"), "attributes": a}


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _mac(payload: bytes) -> str:
    return hmac.new(_key(), payload, hashlib.sha256).hexdigest()


def sign(facility: str, job_spec: dict) -> str:
    """The plan_signature for a launch_plan job_spec on `facility`."""
    payload = _canon({"facility": facility, **_normal(job_spec.get("resources"), job_spec.get("attributes"))})
    return f"{PREFIX}.{base64.urlsafe_b64encode(payload).decode().rstrip('=')}.{_mac(payload)}"


def _signed(signature: str) -> dict | None:
    """The payload a signature carries, or None when it is not one of ours."""
    parts = (signature or "").strip().split(".")
    if len(parts) != 4 or ".".join(parts[:2]) != PREFIX:
        return None
    body, mac = parts[2], parts[3]
    try:
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(_mac(payload), mac):
        return None
    try:
        return json.loads(payload)
    except ValueError:
        return None


def _diffs(got, want, where: str) -> list[str]:
    if isinstance(want, dict) and isinstance(got, dict):
        out = []
        for k in sorted(set(want) | set(got)):
            out += _diffs(got.get(k), want.get(k), f"{where}.{k}")
        return out
    return [] if got == want else [f"{where} is {json.dumps(got)}, the plan signed {json.dumps(want)}"]


def seconds(duration) -> int | None:
    """attributes.duration (int seconds, HH:MM:SS or D-HH:MM:SS) in seconds."""
    if isinstance(duration, int) and not isinstance(duration, bool):
        return duration
    m = re.fullmatch(r"(?:(\d+)-)?(\d+):(\d\d):(\d\d)", str(duration or "").strip())
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def _partition_cores(facility: str, queue: str) -> int | None:
    try:
        parts = config.load_facts(facility).get("partitions") or []
    except (OSError, ValueError):
        return None
    for p in parts if isinstance(parts, list) else []:
        if isinstance(p, dict) and p.get("name") == queue and p.get("cores_per_node"):
            return int(p["cores_per_node"])
    return None


def worst_case(facility: str, spec: JobSpec) -> dict | None:
    """The most a job can cost: walltime x nodes x cores per node, in core-hours.

    Cores per node: processes x cores per process (a GPU plan's cores per
    process are what a GPU is billed as); the partition's whole node when
    exclusive_node_use is set. None when the walltime cannot be read."""
    wall = seconds(spec.attributes.duration)
    if wall is None:
        return None
    res = spec.resources
    nodes = res.node_count
    procs = res.process_count or res.processes_per_node * nodes
    cores = procs * (res.cpu_cores_per_process or 1) / nodes
    if res.exclusive_node_use:
        cores = max(cores, _partition_cores(facility, spec.attributes.queue_name) or 0)
    cores = int(cores) if float(cores).is_integer() else round(cores, 2)
    return {"walltime_s": wall, "nodes": nodes, "cores_per_node": cores,
            "core_hours": round(wall * nodes * cores / 3600, 2)}


def budget(facility: str, account: str | None) -> float | None:
    """The core-hour budget for one job on `facility` charged to `account`."""
    section = config.file_config(facility).get("submit_guard") or {}
    b = section.get("budget_core_hours") if isinstance(section, dict) else None
    if isinstance(b, dict):
        b = b.get(account or "", b.get("*"))
    if b is None:
        return None
    try:
        return float(b)
    except (TypeError, ValueError):
        raise ValueError(f"submit_guard.budget_core_hours in {config.config_path(facility)} must be a "
                         f"number or a mapping of account to number, not {b!r}")


def check_signature(facility: str, spec: JobSpec) -> None:
    """Raise unless `spec` carries a valid launch_plan signature for `facility`
    and its resources and attributes are the signed ones. Call before the
    facility's apply_defaults, on the spec as the agent sent it."""
    if not spec.plan_signature:
        raise ValueError(REFUSED + "the spec has no plan_signature, so it did not come from launch_plan. "
                         "Call launch_plan, merge its job_spec (plan_signature included) into the spec, "
                         "and add only name, directory, executable and attributes.duration. If the task "
                         "only asks to show, check or read, do not submit.")
    signed = _signed(spec.plan_signature)
    if signed is None:
        raise ValueError(REFUSED + "plan_signature is not valid (altered, or from another key). Use the "
                         "job_spec launch_plan returns, unchanged.")
    if signed.get("facility") != facility:
        raise ValueError(REFUSED + f"plan_signature was made for facility {signed.get('facility')!r}, "
                         f"not {facility!r}. Call launch_plan for {facility!r}.")
    got = _normal(spec.resources, spec.attributes)
    diffs = _diffs(got, {k: signed.get(k) for k in got}, "spec")
    if diffs:
        raise ValueError(REFUSED + "the spec's resources or attributes are not the plan's: "
                         + "; ".join(diffs) + ". Use the launch_plan job_spec's resources and attributes "
                         "as given (add only attributes.duration, attributes.account or "
                         "custom_attributes.signal), or call launch_plan again with what you need "
                         "(e.g. nodes=...).")


def check_budget(facility: str, spec: JobSpec) -> None:
    """Raise when the job's worst case is over the budget. Call after
    apply_defaults, so the account and queue are the ones billed."""
    b = budget(facility, spec.attributes.account)
    if b is None:
        return
    wc = worst_case(facility, spec)
    if wc is None:
        raise ValueError(REFUSED + f"attributes.duration {spec.attributes.duration!r} cannot be read, so "
                         f"the worst case cannot be checked against the {b:g} core-h budget.")
    if wc["core_hours"] > b:
        h = wc["walltime_s"]
        raise ValueError(
            REFUSED + f"worst case {wc['core_hours']:g} core-h ({h // 3600:02d}:{h % 3600 // 60:02d}:"
            f"{h % 60:02d} x {wc['nodes']} node(s) x {wc['cores_per_node']} cores) is over the budget of "
            f"{b:g} core-h for {facility}"
            + (f" account {spec.attributes.account}" if spec.attributes.account else "")
            + ". Shorten attributes.duration or use fewer nodes, or ask the user to raise "
            f"submit_guard.budget_core_hours in {config.config_path(facility)}.")


def dry_run(facility: str, spec: JobSpec, backend) -> dict:
    """submit_job(dry_run=True): what a real submit_job would do with `spec`,
    without submitting. Runs the guard's signature check (on the spec as
    sent), the facility's defaults and validation, the budget check and the
    script render, in submit_job's order, and collects every refusal instead
    of stopping at the first; `refusals[0]` is the error a real submit would
    raise. With the guard off only the facility's validation can refuse; the
    worst case and budget are still reported, for information."""
    guarded = enabled()
    refusals, script, wc, b = [], None, None, None
    if guarded:
        try:
            check_signature(facility, spec)
        except ValueError as e:
            refusals.append(str(e))
    try:
        backend.apply_defaults(spec)
        backend.validate_spec(spec)
    except ValueError as e:
        refusals.append(str(e))
    else:
        wc = worst_case(facility, spec)
        try:
            b = budget(facility, spec.attributes.account)
            if guarded:
                check_budget(facility, spec)
        except ValueError as e:
            refusals.append(str(e))
        script = backend.render_script(spec)
    return {"dry_run": True, "submitted": False,
            "guard": "on" if guarded else "off",
            "verdict": "refuse" if refusals else "accept",
            "refusals": refusals,
            "worst_case": wc, "budget_core_hours": b,
            "account": spec.attributes.account, "queue_name": spec.attributes.queue_name,
            "script": script}
