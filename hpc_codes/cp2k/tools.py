"""CP2K tools: parse benchmark stages, parse-check an input, check a build,
look up a build's &KIND facts, write benchmark inputs.

All five only read on the cluster; bench_input writes its inputs on this
machine. Each runs one shell command on the
login node through middleware.run_command, after checking that the
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
from hpc_codes.cp2k import bench_input as _bench

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


def bench_input(facility: str, xyz_path: str, name: str, out_dir: str,
                build_id: str | None = None, steps: int = 10, smoke_steps: int = 3,
                xc: str = "revPBE", d3: str = "zero", cutoff: int = 450, rel_cutoff: int = 60,
                basis: str = "DZVP-MOLOPT-SR-GTH", charge: int = 0,
                overwrite: bool = False) -> dict:
    pack_values(facility)
    config.require_facility_configuration(facility)
    src = Path(xyz_path).expanduser()
    if not src.is_file():
        raise ValueError(f"{src} is not a file on this machine. For a file on a "
                         "cluster, download it first (fs_download).")
    text = src.read_text()
    _bench.check_args(name, xc, d3)
    elements = list(dict.fromkeys(_bench.read_extxyz(text)[1]))
    out = Path(out_dir).expanduser()
    targets = [out / f"{name}.inp", out / f"{name}-smoke.inp"]
    existing = [str(t) for t in targets if t.exists()]
    if existing and not overwrite:
        raise ValueError(f"{existing} exist; pass overwrite=true to replace them")
    k = kinds(facility, elements, basis, build_id)
    if k["errors"]:
        raise ValueError(f"no {basis} kind in {k['data_dir']} for: {k['errors']}")
    texts, info = _bench.bench_inputs(text, src.name, name, k["kinds"], steps, smoke_steps,
                                      xc, d3, cutoff, rel_cutoff, charge)
    out.mkdir(parents=True, exist_ok=True)
    for fname, body in texts.items():
        (out / fname).write_text(body)
    return {"inputs": [str(t) for t in targets], "build_id": k["build_id"], **info}
