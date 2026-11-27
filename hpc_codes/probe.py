"""facility_probe: what S0 checks by hand before installing or benchmarking a code.

One read-only command on the login node (remote Python and its version, the
login node's internet), plus local facts: the submit guard (HPC_SUBMIT_GUARD
in this server's environment and the budget in ~/.hpc-agent/<slug>.json),
the scheduler (the facility's bundled facts) and the code pack's launcher
lines. Facility-neutral: the pack values are read by name from the code
pack (<CODE>_PYTHON, <CODE>_LAUNCH, <CODE>_BENCH_LAUNCH) when the facility
opted into it. Nothing is written anywhere.
"""
import re
import shlex

from hpc_agent_core import config, submit_guard
from hpc_agent_core.middleware import run_command
from hpc_codes import packs

#: the oldest remote Python the helper scripts and tools are kept working on
MIN_PYTHON = (3, 10)
#: a well-known HTTPS page for the login node's internet check (one bounded GET)
INTERNET_URL = "https://pypi.org/simple/"
INTERNET_TIMEOUT_S = 5


def python_lines(python: str) -> str:
    """Shell lines printing PY_PATH=<path> and PY_VERSION=<x.y.z> for `python`."""
    q = shlex.quote(python)
    return (f'p=$(command -v {q} 2>/dev/null); echo "PY_PATH=$p"; '
            '[ -n "$p" ] && "$p" -c \'import sys; print("PY_VERSION=%d.%d.%d" % sys.version_info[:3])\' '
            '2>/dev/null; ')


def python_fields(lines: list[str]) -> dict:
    """{path, version, ok} from the lines python_lines printed."""
    get = lambda key: next((l.split("=", 1)[1].strip() for l in lines if l.startswith(key + "=")), "")
    version = get("PY_VERSION") or None
    nums = tuple(int(x) for x in re.findall(r"\d+", version or "")[:2])
    return {"path": get("PY_PATH") or None, "version": version,
            "ok": bool(version) and len(nums) == 2 and nums >= MIN_PYTHON}


def guard_fields(facility: str) -> dict:
    """The submit guard as this server sees it, all local: on/off, and the
    per-job core-hour budget in the facility's user config."""
    path = config.config_path(facility)
    out = {"enabled": submit_guard.enabled(), "config_path": str(path), "config_present": path.exists(),
           "budget_core_hours": None, "budget_present": False, "error": None}
    try:
        cfg = config.file_config(facility)
        account = ((cfg.get("defaults") or {}).get("account") if isinstance(cfg, dict) else None)
        out["budget_core_hours"] = submit_guard.budget(facility, account)
    except (RuntimeError, ValueError) as e:
        out["error"] = str(e)
    out["budget_present"] = out["budget_core_hours"] is not None
    return out


def probe(facility: str, code: str = "cp2k") -> dict:
    fac = config.require_facility_configuration(facility)
    prefix = code.upper().replace("-", "_")
    try:
        vals = packs.pack_values(facility, code)
    except ValueError:
        vals = {}
    python = str(vals.get(f"{prefix}_PYTHON") or "python3")
    launch = vals.get(f"{prefix}_LAUNCH")
    launcher = launch.get("launcher") if isinstance(launch, dict) else None
    try:
        scheduler = config.load_facts(facility).get("scheduler")
    except (OSError, ValueError):
        scheduler = None
    body = (python_lines(python)
            + 'if command -v curl >/dev/null 2>&1; then '
            f'c=$(curl -sS -m {INTERNET_TIMEOUT_S} -o /dev/null -w "%{{http_code}}" '
            f'{shlex.quote(INTERNET_URL)} 2>/dev/null); echo "NET_CODE=${{c:-000}}"; '
            'else echo NET_CODE=nocurl; fi; true')
    out = run_command(fac.slug, f"( {body} ) </dev/null 2>&1", timeout=60)
    lines = out.splitlines()
    code_line = next((l.split("=", 1)[1].strip() for l in lines if l.startswith("NET_CODE=")), "")
    http = code_line if re.fullmatch(r"\d{3}", code_line) else None
    return {
        "facility": fac.slug,
        "code": code,
        "python": {"command": python, "source": f"{prefix}_PYTHON" if vals.get(f"{prefix}_PYTHON") else "python3",
                   **python_fields(lines), "min": ".".join(map(str, MIN_PYTHON))},
        "internet": {"url": INTERNET_URL, "http_code": http, "curl": code_line != "nocurl",
                     "ok": bool(http) and http != "000" and int(http) < 400},
        "guard": guard_fields(fac.slug),
        "scheduler": scheduler,
        "launcher": launcher,
        "bench_launcher": vals.get(f"{prefix}_BENCH_LAUNCH"),
        "pack": bool(vals),
    }
