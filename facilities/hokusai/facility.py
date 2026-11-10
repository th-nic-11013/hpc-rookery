"""HOKUSAI BigWaterfall2 (HBW2) registration — see PORTING.md.

HBW2 is Slurm with accounting on (every job is billed to a project under a
fair-share budget, so sacct/sacctmgr/sshare are available), requests GPUs
with the job-total `--gpus=N` flag, has a single GPU vendor (NVIDIA H100 ->
`--nv` for containers), and lets Slurm derive node count from the GPU count
(so `--nodes` is only emitted when the caller asks for more than one node).
That's the first row of PORTING.md §6's dialect table — every other knob
keeps its default.

Two things make HBW2 the first facility to need extension points beyond a
plain SlurmBackend construction:

1. **`--account` is mandatory on every job**, and the project ID is a
   *per-user* choice, not a bundled cluster fact — so apply_defaults()
   reads it from the user's own config file (or an env var) and raises a
   clear, actionable error when none can be resolved, rather than letting
   sbatch reject the job with its own opaque message.
2. **Fair-share standing governs when a queued job starts**, so
   get_projects() reports it alongside the plain associations the base
   SlurmBackend already returns.
3. **The budget lives in `listcpu`, not in Slurm** (gap G35): core-hours
   used and granted per subsystem (mpc, lmc, gpu, fpga) with an expiry
   date. get_project_allocations() adds those rows; get_user_allocations()
   adds the current user's own row. The command also lists every other
   member's usage, which neither returns.
"""
import os
import re
from pathlib import Path

from hpc_agent_core import config
from hpc_agent_core.compute.slurm import SlurmBackend
from hpc_agent_core.middleware import run_command
from hpc_agent_core.models import JobSpec
from facilities.registry import register_backend

SLUG = "hokusai"

FACILITY = config.register_facility(
    slug=SLUG,
    display_name="HOKUSAI BigWaterfall2 (HBW2)",
    description="RIKEN HOKUSAI BigWaterfall2: CPU-first Slurm cluster "
                 "(312-node MPC partition, plus large-memory and NVIDIA H100 "
                 "GPU subsystems). Requires a project account on every job.",
    default_host="hokusai",
    data_dir=Path(__file__).parent / "data",
    embed_base_url="http://llm.ai.r-ccs.riken.jp:11434/v1",  # shared RIKEN R-CCS endpoint
    embed_model="bge-m3:567m",
    docs_cite_url="",  # the HBW2 portal is auth-gated, not a stable public docs URL
    config_example={
        "ssh": {"host": "hokusai"},
        "defaults": {"account": "RB99999"},
    },
    setup_help=(
        "HBW2 accepts key-based SSH only — there are no password prompts.\n"
        "Register your public key at https://hokusai.riken.jp/hbw2/ before the\n"
        "first login, then either add a 'hokusai' alias to ~/.ssh/config\n"
        "pointing at hokusai.riken.jp, or set ssh.host to user@hokusai.riken.jp.\n"
        "Set defaults.account to the project to bill: every HBW2 job requires\n"
        "one (RIKEN IDs start RB, HPCI-derived ones start HP).\n"
        "Running on an HBW2 front-end node instead of a laptop? Use\n"
        "\"host\": \"localhost\" and no SSH key is needed at all."
    ),
)


def _default_account() -> str | None:
    """The project/account to charge when a job doesn't name one.

    Resolved HOKUSAI_ACCOUNT > the user config's `defaults.account` > the
    pre-migration top-level `account` key. That last fallback matters: an
    older HBW2 README's example config was `{"ssh": ..., "account":
    "RB999999"}`, and dropping it would silently strip the default from
    anyone with a config file predating the `defaults` object, leaving them
    hitting the "name a project" error on every submit.
    """
    cfg = config.file_config(SLUG)
    return (os.environ.get(f"{FACILITY.env_prefix}_ACCOUNT")
            or (cfg.get("defaults") or {}).get("account")
            or cfg.get("account"))


def _num(v: str) -> float | None:
    return None if v.strip() in ("", "-") else float(v)


def parse_listcpu(text: str, user: str | None = None) -> dict:
    """`listcpu -p <project>` -> {project, title, subsystems, user}.

    The first block (after the dashed rule, up to a blank line) is one row
    per subsystem: name, used h, limit h, used %, expiry. The next block is
    one row per member (used h, "-", "-", expiry; a trailing "*" on a name
    is HOKUSAI's mark). Only `user`'s own row is kept; the others are not
    returned. Refuses text it doesn't recognise rather than guessing.
    """
    lines = text.splitlines()
    head = next((l for l in lines if l.strip()), "")
    m = re.match(r"\s*\[([^\]]+)\]\s*(.*)", head)
    rule = next((i for i, l in enumerate(lines) if set(l.strip()) <= {"-", " "} and "--" in l), None)
    if not m or rule is None:
        raise RuntimeError("listcpu output not recognised (no '[project] title' line or no "
                           f"dashed rule): {text[:300]!r}")
    blocks, cur = [], []
    for l in lines[rule + 1:]:
        if l.strip():
            cur.append(l.split())
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    subsystems = []
    for row in blocks[0] if blocks else []:
        if len(row) != 5:
            raise RuntimeError(f"listcpu subsystem row not recognised: {' '.join(row)!r}")
        subsystems.append({"subsystem": row[0], "used_h": _num(row[1]), "limit_h": _num(row[2]),
                           "used_pct": _num(row[3]), "expiry": row[4]})
    mine = None
    for row in (blocks[1] if len(blocks) > 1 else []):
        if user and len(row) == 5 and row[0].rstrip("*") == user:
            mine = {"user": user, "used_h": _num(row[1]), "expiry": row[4],
                    "marked": row[0].endswith("*")}
    return {"project": m.group(1), "title": m.group(2).strip(),
            "subsystems": subsystems, "user": mine}


def _listcpu(facility: str, project_id: str) -> tuple[dict, str]:
    """Run listcpu for one project; return the parse and the login name."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", project_id or ""):
        raise ValueError(f"project id {project_id!r} is not a HOKUSAI project (e.g. RB230090)")
    out = run_command(facility, f'echo "ROOKERY_USER=$USER"; listcpu -p {project_id}')
    first, _, rest = out.partition("\n")
    user = first.split("=", 1)[1].strip() if first.startswith("ROOKERY_USER=") else None
    return parse_listcpu(rest, user), user


class HokusaiBackend(SlurmBackend):
    def apply_defaults(self, spec: JobSpec) -> None:
        """Fill in HBW2's default partition and the user's default project.

        HBW2 requires an account on every job, so a spec with no account
        and no configured default fails here with a message naming the fix,
        rather than reaching sbatch.
        """
        if not spec.attributes.queue_name:
            spec.attributes.queue_name = (
                config.load_facts(SLUG).get("defaults", {}).get("partition", "mpc")
            )
        if spec.attributes.account is None:
            spec.attributes.account = _default_account()
        if not spec.attributes.account:
            raise ValueError(
                "No project named. Every HBW2 job is billed to a project, so "
                "--account is mandatory. Set spec.attributes.account to a "
                "project ID (RIKEN 'RB...' or HPCI 'HP...'), or configure a "
                f"default under defaults.account in {config.config_path(SLUG)}. "
                "Use get_projects to see which accounts you may charge."
            )

    def get_projects(self) -> list[dict]:
        """The base associations, plus each account's fair-share standing.

        On HBW2 the fair-share number is the thing that actually explains
        when a queued job will start, so it's worth the second round trip.
        It moves continuously — read it live rather than assuming a cached
        value.
        """
        projects = super().get_projects()
        share = run_command(
            self.facility,
            "sshare -U --parsable2 --noheader --format=Account,FairShare,RawUsage",
        )
        fairshare: dict[str, dict] = {}
        for line in share.strip().splitlines():
            parts = line.split("|")
            if len(parts) >= 3 and parts[0].strip():
                fairshare[parts[0].strip()] = {
                    "fairshare": parts[1].strip(),
                    "raw_usage": parts[2].strip(),
                }
        return [{**p, **fairshare.get(p["account"], {})} for p in projects]

    def _with_slurm_limits(self, fetch) -> dict:
        """The base Slurm association limits, or why there are none: on
        HBW2 the budget is listcpu's, so an empty association is no error."""
        try:
            return fetch()
        except (RuntimeError, ValueError) as exc:
            return {"slurm_limits_error": str(exc)}

    def get_project_allocations(self, project_id: str) -> dict:
        """Slurm's account limits plus `listcpu`'s per-subsystem budget:
        `subsystems` = [{subsystem, used_h, limit_h, used_pct, expiry}]
        (hours as listcpu prints them for each subsystem). Other
        members' usage, which listcpu also prints, is not returned."""
        base = self._with_slurm_limits(lambda: super(HokusaiBackend, self)
                                       .get_project_allocations(project_id))
        lc, _ = _listcpu(self.facility, project_id)
        return {**base, "project": lc["project"], "title": lc["title"],
                "subsystems": lc["subsystems"], "source": "listcpu -p"}

    def get_user_allocations(self, project_id: str) -> dict:
        """Slurm's per-user limits plus the current user's own `listcpu`
        row: `usage` = {user, used_h, expiry, marked} (None if listcpu has
        no row for this user under the project)."""
        base = self._with_slurm_limits(lambda: super(HokusaiBackend, self)
                                       .get_user_allocations(project_id))
        lc, _ = _listcpu(self.facility, project_id)
        return {**base, "project": lc["project"], "usage": lc["user"], "source": "listcpu -p"}


BACKEND = HokusaiBackend(
    facility=SLUG,
    has_accounting=True,
    gpu_request_style="gpus_total",
    # jobs_dir defaults to "agent/jobs" -> ~/agent/jobs, per the visible-
    # directory bias; no reason to override it on HBW2.
)
register_backend(SLUG, BACKEND)
