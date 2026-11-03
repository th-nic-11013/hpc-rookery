"""CP2K tools: parse benchmark stages, parse-check an input, check a build.

All three only read on the cluster. Each runs one shell command on the
login node through middleware.run_command, after checking that the
facility opted into the cp2k pack and then the same configuration check
the scheduler tools use. The facility's CP2K values (front-end
Python, cores per node) come from facilities/<dir>/codes/cp2k/values.json,
read at call time; the build comes from software_registry.
"""
import json
import shlex
from pathlib import Path

from hpc_agent_core import config
from hpc_agent_core.middleware import norm_path, run_command
from hpc_codes import records

PARSE_BENCH = Path(__file__).parent / "parse_bench.py"
_EOF = "HPC_CODES_EOF"


def pack_values(facility: str, code: str = "cp2k") -> dict:
    path = config.get_facility(facility).data_dir.parent / "codes" / code / "values.json"
    if not path.exists():
        raise ValueError(f"{facility} has not opted into the {code} code pack "
                         f"(no codes/{code}/values.json), so these tools don't apply there.")
    return json.loads(path.read_text())


def _home_path(path: str) -> str:
    """A shell word for a remote path that stays right after a `cd`."""
    p = norm_path(path)
    return shlex.quote(p) if p.startswith("/") else '"$HOME"/' + shlex.quote(p)


def resolve_build(facility: str, build_id: str | None = None) -> dict:
    """The software_registry record to use: the named one, else the default."""
    filters = {"code": "cp2k", **({"build_id": build_id} if build_id else {"default": True})}
    hits = [r for r in records.query(facility, "software_registry", filters) if not r.get("removed")]
    if not hits:
        known = [r["build_id"] for r in records.query(facility, "software_registry", {"code": "cp2k"})
                 if not r.get("removed")]
        what = f"build {build_id!r}" if build_id else "default CP2K build"
        raise ValueError(f"No {what} in {facility}'s software_registry (removed builds don't "
                         f"count). Available: {', '.join(known) or 'none'}. "
                         f"See {facility}-cp2k-build.")
    return hits[0]


def bench_parse(facility: str, stage_dirs: list[str]) -> dict:
    vals = pack_values(facility)
    config.require_facility_configuration(facility)
    if not stage_dirs:
        raise ValueError("give at least one stage folder, e.g. ~/agent/work/cp2k-runs/<system>/bench/runs/<jobid>/stage2")
    src = PARSE_BENCH.read_text()
    if _EOF in src:
        raise RuntimeError(f"parse_bench.py contains the here-document marker {_EOF}")
    dirs = " ".join(_home_path(d) for d in stage_dirs)
    cmd = (f"{shlex.quote(vals['CP2K_PYTHON'])} -I - --cores={int(vals['CP2K_CORES_PER_NODE'])} "
           f"--json {dirs} <<'{_EOF}'\n{src}\n{_EOF}")
    out = run_command(facility, cmd)
    result = json.loads(out.strip().splitlines()[-1])
    # report the folders as the caller named them, not as the shell spelled them
    result["stages"] = dict(zip(stage_dirs, result["stages"].values()))
    return result


def _run_with_exit(facility: str, body: str) -> tuple[int, list[str]]:
    """Run body in a subshell; return its exit code and output lines."""
    out = run_command(facility, f"( {body} ) 2>&1; echo HPC_CODES_EXIT=$?")
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
