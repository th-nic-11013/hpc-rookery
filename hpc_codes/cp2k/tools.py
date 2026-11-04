"""CP2K tools: parse benchmark stages, parse-check an input, check a build,
look up a build's &KIND facts, dry-run a benchmark folder.

All of them only read on the cluster (the dry run writes a scratch folder
of its own under ~/agent/work/ and removes it). Each runs one shell command
(the dry run one per case) on the login node through
middleware.run_command, after checking that the
facility opted into the cp2k pack and then the same configuration check
the scheduler tools use. The facility's CP2K values (front-end
Python, cores per node) come from facilities/<dir>/codes/cp2k/values.json,
read at call time; the build comes from software_registry.
"""
import json
import re
import shlex
from pathlib import Path

from hpc_agent_core import config
from hpc_agent_core.middleware import norm_path, run_command
from hpc_codes import packs

PARSE_BENCH = Path(__file__).parent / "parse_bench.py"
KINDS = Path(__file__).parent / "kinds.py"
_EOF = "HPC_CODES_EOF"


def _home_path(path: str) -> str:
    """A shell word for a remote path that stays right after a `cd`."""
    p = norm_path(path)
    return shlex.quote(p) if p.startswith("/") else '"$HOME"/' + shlex.quote(p)


def pack_values(facility: str, code: str = "cp2k") -> dict:
    return packs.pack_values(facility, code)


def resolve_build(facility: str, build_id: str | None = None) -> dict:
    return packs.resolve_build(facility, "cp2k", build_id)


def _python_script(vals: dict, script: Path, args: str) -> str:
    """A command that runs a local script with the facility's front-end Python."""
    src = script.read_text()
    if _EOF in src:
        raise RuntimeError(f"{script.name} contains the here-document marker {_EOF}")
    return f"{shlex.quote(vals['CP2K_PYTHON'])} -I - {args} <<'{_EOF}'\n{src}\n{_EOF}"


def bench_parse(facility: str, stage_dirs: list[str]) -> dict:
    vals = pack_values(facility)
    config.require_facility_configuration(facility)
    if not stage_dirs:
        raise ValueError("give at least one stage folder, e.g. ~/agent/work/cp2k-runs/<system>/bench/runs/<jobid>/stage2")
    dirs = " ".join(_home_path(d) for d in stage_dirs)
    cmd = _python_script(vals, PARSE_BENCH, f"--cores={int(vals['CP2K_CORES_PER_NODE'])} --json {dirs}")
    out = run_command(facility, cmd)
    result = json.loads(out.strip().splitlines()[-1])
    # report the folders as the caller named them, not as the shell spelled them
    result["stages"] = dict(zip(stage_dirs, result["stages"].values()))
    return result


def _run_with_exit(facility: str, body: str) -> tuple[int, list[str]]:
    """Run body in a subshell; return its exit code and output lines.

    stdin is /dev/null: run_command's ssh leaves it open, and cp2k.psmp
    (MPI start-up) waits on an open stdin until the transport times out.
    """
    out = run_command(facility, f"( {body} ) </dev/null 2>&1; echo HPC_CODES_EXIT=$?")
    lines = out.rstrip("\n").splitlines()
    code = int(lines[-1].split("=", 1)[1]) if lines and lines[-1].startswith("HPC_CODES_EXIT=") else -1
    return code, lines[:-1]


def check_input(facility: str, input_path: str, build_id: str | None = None) -> dict:
    pack_values(facility)
    config.require_facility_configuration(facility)
    build = resolve_build(facility, build_id)
    folder, name = (input_path.rsplit("/", 1) if "/" in input_path else (".", input_path))
    body = (f"cd {_home_path(folder or '/')} && source {_home_path(build['env_script'])} >/dev/null 2>&1 "
            f"&& OMP_NUM_THREADS=1 timeout 60 cp2k.psmp --check -i {shlex.quote(name)}")
    code, lines = _run_with_exit(facility, body)
    # CP2K reports a bad input inside a box of *** lines ([ABORT] and the
    # message); a missing env or binary shows up as a shell error line
    errors = [l.strip(" *") for l in lines
              if (l.lstrip().startswith("***") or "ERROR" in l or "command not found" in l
                  or "No such file" in l) and l.strip(" *")]
    return {"ok": code == 0, "build_id": build["build_id"], "exit_code": code,
            "timed_out": code == 124, "errors": errors[:20], "tail": lines[-40:]}


def check_build(facility: str, build_id: str | None = None) -> dict:
    pack_values(facility)
    config.require_facility_configuration(facility)
    build = resolve_build(facility, build_id)
    env = _home_path(build["env_script"])
    data = _home_path(build["data_dir"]) if build.get("data_dir") else "''"
    body = (f"[ -f {env} ] && echo ENV=ok || echo ENV=missing; "
            f"[ -n {data} ] && {{ [ -d {data} ] && echo DATA=ok || echo DATA=missing; }}; "
            f"source {env} >/dev/null 2>&1; b=$(command -v cp2k.psmp); echo BIN=$b; "
            f'[ -n "$b" ] && {{ ldd "$b" | grep "not found" | sed "s/^/MISSING=/"; '
            f'cp2k.psmp --version 2>/dev/null | grep -E "CP2K version|cp2kflags"; }}; true')
    _, lines = _run_with_exit(facility, body)
    get = lambda key: next((l.split("=", 1)[1] for l in lines if l.startswith(key + "=")), "")
    version = next((l.split("CP2K version", 1)[1].strip() for l in lines if "CP2K version" in l), None)
    flags = next((l.split(":", 1)[1].split() for l in lines if "cp2kflags" in l), [])
    missing = [l.split("=", 1)[1].split()[0] for l in lines if l.startswith("MISSING=")]
    binary = get("BIN") or None
    return {"build_id": build["build_id"], "env_script_ok": get("ENV") == "ok",
            "binary": binary, "version": version, "cp2kflags": flags, "missing_libs": missing,
            "data_dir_ok": get("DATA") == "ok" if build.get("data_dir") else None,
            "ok": bool(binary) and not missing and get("ENV") == "ok"
                  and get("DATA") != "missing" and version == build.get("version")}


def kinds(facility: str, elements: list[str], basis: str = "DZVP-MOLOPT-SR-GTH",
          build_id: str | None = None) -> dict:
    vals = pack_values(facility)
    config.require_facility_configuration(facility)
    build = resolve_build(facility, build_id)
    if not build.get("data_dir"):
        raise ValueError(f"{build['build_id']} has no data_dir in software_registry; "
                         f"record it (see {facility}-cp2k-build).")
    elements = [e.strip() for e in elements if e and e.strip()]
    if not elements:
        raise ValueError('give the elements, e.g. ["Zr", "O", "C", "H"]')
    bad = [e for e in elements if not re.fullmatch(r"[A-Z][a-z]?", e)]
    if bad:
        raise ValueError(f"not element symbols: {bad}")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", basis):
        raise ValueError(f"not a basis set name: {basis!r}")
    args = " ".join([_home_path(build["data_dir"]), shlex.quote(basis), *elements])
    out = run_command(facility, _python_script(vals, KINDS, args))
    result = json.loads(out.strip().splitlines()[-1])
    return {"build_id": build["build_id"], "data_dir": build["data_dir"], **result}


DRYRUN_CASES = ("pass", "fail_layout", "time_limit")
# set by the tool or by dryrun.sh itself; a setting must not override them
_DRYRUN_RESERVED = {"PATH", "HOME", "BASH_ENV", "ENV", "IFS", "BENCH", "BENCH_START", "REF_OUT",
                    "FAIL_LAYOUT", "JOB", "DRY_NODES"}
_DRYRUN_GUARD = "stub srun is not first on PATH"
DRYRUN_SCRATCH = "agent/work/cp2k-dryrun"


def _dryrun_env(settings: dict) -> list[str]:
    words = []
    for k, v in (settings or {}).items():
        if (not re.fullmatch(r"[A-Z][A-Z0-9_]*", k) or k in _DRYRUN_RESERVED
                or k.startswith(("SLURM_", "LD_", "DYLD_"))):
            raise ValueError(f"not a benchmark setting the dry run may take: {k!r}")
        words.append(f"{k}={shlex.quote(str(v))}")
    return words


def _dryrun_expected(case: str, code: int, lines: list[str], job: bool, layout: str | None):
    if case == "pass":
        ok = code == 0 and (job or any("all stages passed" in l for l in lines))
        return ok, "" if ok else "the dry run did not pass"
    if case == "fail_layout":
        hit = any("FAILED:" in l and layout in l for l in lines)
        ok = code != 0 and hit and not any("all stages passed" in l for l in lines)
        return ok, "" if ok else f"the job did not stop on the failing layout {layout}"
    ok = code != 0 and any("NOT STARTED" in l for l in lines)
    return ok, "" if ok else "the time guard did not refuse a run"


def bench_dryrun(facility: str, bench_dir: str, ref_out: str, settings: dict | None = None,
                 cases: list[str] | None = None, fail_layout: str | None = None,
                 time_limit_s: int = 60, job_script: str | None = None, nodes: int = 4,
                 keep_scratch: bool = False) -> dict:
    vals = pack_values(facility)
    config.require_facility_configuration(facility)
    cases = list(cases or DRYRUN_CASES)
    bad = [c for c in cases if c not in DRYRUN_CASES]
    if bad or not cases:
        raise ValueError(f"unknown dry-run cases {bad}; choose from {list(DRYRUN_CASES)}")
    if job_script is not None and not re.fullmatch(r"[A-Za-z0-9._-]+\.sh", job_script):
        raise ValueError(f"job_script is a script name in bench_dir, e.g. multinode_layouts_job.sh: {job_script!r}")
    if int(nodes) < 1:
        raise ValueError("nodes must be at least 1")
    settings = {k: str(v) for k, v in (settings or {}).items()}
    env = _dryrun_env(settings)
    job = job_script is not None
    if "fail_layout" in cases and not fail_layout:
        if job:
            layouts = settings.get("LAYOUTS", "").split()
        else:
            smoke = set(settings.get("SMOKE_LAYOUTS", vals.get("CP2K_SMOKE_LAYOUTS", "")).split())
            scan = settings.get("SCAN_LAYOUTS", vals.get("CP2K_SCAN_LAYOUTS", "")).split()
            layouts = [l for l in scan if l not in smoke] or scan
        if not layouts:
            raise ValueError("give fail_layout, or LAYOUTS / SCAN_LAYOUTS in settings")
        # the second per-node layout of a multi-node job: the first is the baseline
        fail_layout = layouts[min(1, len(layouts) - 1)] if job else layouts[-1]
    if fail_layout and not re.fullmatch(r"\d+x\d+", fail_layout):
        raise ValueError(f"fail_layout is <ranks>x<threads>, e.g. 8x14: {fail_layout!r}")

    bench = _home_path(bench_dir)
    script = f"{bench}/dryrun.sh"
    results = []
    for case in cases:
        case_env = list(env)
        if case == "fail_layout":
            r, t = (int(x) for x in fail_layout.split("x"))
            # the stub srun sees the job's total ranks; a multi-node layout is per node
            case_env.append(f"FAIL_LAYOUT={r * int(nodes) if job else r}x{t}")
        if case == "time_limit":
            case_env.append(f"BENCH_LIMIT_S={int(time_limit_s)}")
        if job:
            case_env += [f"JOB={job_script}", f"DRY_NODES={int(nodes)}"]
        case_env.append(f"REF_OUT={_home_path(ref_out)}")
        # a scratch folder of the tool's own, removed afterwards: the benchmark
        # folder is only read
        body = (f"grep -q {shlex.quote(_DRYRUN_GUARD)} {script} || {{ echo HPC_DRYRUN_NO_GUARD; exit 97; }}; "
                f'mkdir -p "$HOME"/{DRYRUN_SCRATCH} && '
                f'S=$(mktemp -d "$HOME"/{DRYRUN_SCRATCH}/{case}.XXXXXX) || exit 98; '
                f"env {' '.join(case_env)} timeout 300 bash {script} {bench} \"$S\" dry-{case}; rc=$?; "
                f'echo "HPC_DRYRUN_SCRATCH=$S"; '
                + ("" if keep_scratch else
                   f'case "$S" in "$HOME"/{DRYRUN_SCRATCH}/{case}.*) rm -rf -- "$S";; esac; ')
                + "exit $rc")
        code, lines = _run_with_exit(facility, body)
        if "HPC_DRYRUN_NO_GUARD" in lines:
            raise RuntimeError(f"{bench_dir}/dryrun.sh is missing or has no stub-srun check; "
                               "upload the benchmark skill's scripts/dryrun.sh first")
        scratch = next((l.split("=", 1)[1] for l in lines if l.startswith("HPC_DRYRUN_SCRATCH=")), None)
        lines = [l for l in lines if not l.startswith("HPC_DRYRUN_SCRATCH=")]
        expected, why = _dryrun_expected(case, code, lines, job, fail_layout)
        results.append({
            "case": case, "exit_code": code, "expected": expected, "why": why,
            "stages_run": [m.group(1) for l in lines
                           if (m := re.fullmatch(r"##### (stage\w+) #####", l.strip()))],
            "failed": [l.split("FAILED:", 1)[1].strip() for l in lines if "FAILED:" in l],
            "not_started": [l.strip("= ").split(" NOT STARTED")[0] for l in lines if "NOT STARTED" in l],
            **({"fail_layout": fail_layout} if case == "fail_layout" else {}),
            **({"scratch": scratch} if keep_scratch else {}),
            "log_tail": lines[-40:]})
    return {"ok": all(r["expected"] for r in results), "bench_dir": bench_dir,
            "job_script": job_script, "cases": results}
