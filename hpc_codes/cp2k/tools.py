"""CP2K tools: parse benchmark stages, parse-check an input, check a build,
look up a build's &KIND facts, write benchmark inputs, dry-run a
benchmark folder, turn benchmark results into records.

All of them only read on the cluster (the dry run writes a scratch folder
of its own under ~/agent/work/ and removes it; bench_input writes its
inputs on this machine; bench_records writes only local records, and
reaches the cluster only to parse). Each runs one shell command
(the dry run one per case) on the login node through
middleware.run_command, after checking that the
facility opted into the cp2k pack and then the same configuration check
the scheduler tools use. The facility's CP2K values (front-end
Python, cores per node) come from facilities/<dir>/codes/cp2k/values.json,
read at call time; the build comes from software_registry.
"""
import datetime
import json
import re
import shlex
from pathlib import Path

from hpc_agent_core import config
from hpc_agent_core.middleware import norm_path, run_command
from hpc_codes import packs, records
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


# After a passing --check, the same login-node command runs `cp2k.psmp --echo`:
# CP2K's own preprocessor and parser print the input as CP2K will run it (@SET
# expanded, only the taken @IF branch, @INCLUDEs read, &EXT_RESTART applied).
# Each echoed line comes back prefixed; on the two lines that name a
# wavefunction the shell also tests, from the input's folder, whether it
# exists: WFN_RESTART_FILE_NAME, and PROJECT_NAME for CP2K's default name
# <PROJECT>-RESTART.wfn when that keyword is unset.
_ECHO = "HPC_ECHO"
_ECHO_LOOP = (
    'while IFS= read -r l; do case "$l" in '
    '*" WFN_RESTART_FILE_NAME "*|*" PROJECT_NAME "*) '
    'p=$(printf "%s" "$l" | sed -e "s/^ *[A-Z_]* //" -e "s/ *$//" -e "s/^\\"//" -e "s/\\"$//"); '
    'case "$l" in *" PROJECT_NAME "*) p="$p-RESTART.wfn";; esac; '
    f'if [ -n "$p" ] && [ -e "$p" ]; then echo "{_ECHO} 1 $l"; else echo "{_ECHO} 0 $l"; fi;; '
    f'*) echo "{_ECHO} - $l";; esac; done')
# SCF_GUESS values that read the wavefunction file
_READS_WFN = ("RESTART", "HISTORY_RESTART")
# the ASCII-art figure at the left of CP2K's [ABORT] / [WARNING] box
_BOX_ART = re.compile(r"^\s*(?:\[ABORT\]|\[WARNING\]|___|/\s+\\|\\___/|O/\||/\| \||/ \\|\|)")


def _box_text(lines: list[str]) -> list[str]:
    """The text of CP2K's * boxes, one entry per line, border and figure removed."""
    out = []
    for l in lines:
        m = re.fullmatch(r"\s*\*(.*)\*\s*", l)
        text = _BOX_ART.sub("", m.group(1)).strip() if m else ""
        if text.strip("*"):
            out.append(text)
    return out


def _boxes(lines: list[str]) -> list[list[str]]:
    """CP2K's boxes: the lines between two rules of stars."""
    boxes, cur = [], None
    for l in lines:
        if re.fullmatch(r"\s*\*{20,}\s*", l):
            if cur:
                boxes.append(cur)
            cur = []
        elif cur is not None and re.fullmatch(r"\s*\*.*\*\s*", l):
            cur.append(l)
        else:
            cur = None
    return boxes


def _abort_missing_file(lines: list[str]) -> str | None:
    """The file CP2K's [ABORT] says does not exist, when the &EXT_RESTART
    handler hit it; a long name is hard-wrapped over box lines."""
    if not any("handle_ext_restart" in l for l in lines):
        return None
    text = " ".join(_box_text(lines))
    m = re.search(r"specified file\s*<([^>]*)>\s*cannot be opened.*does not\s*exist", text)
    return re.sub(r"\s+", "", m.group(1)) if m else None


def _ext_restart_read(lines: list[str]) -> str | None:
    """The file named in CP2K's RESTART INFORMATION box (it was read)."""
    box = _box_text(lines)
    for i, t in enumerate(box):
        if t.startswith("RESTART FILE NAME:"):
            name = t.split(":", 1)[1].strip()
            for more in box[i + 1:]:          # continuation lines of a wrapped name
                if ":" in more or " " in more:
                    break
                name += more
            return name
    return None


def _wfn_files(echo: list[tuple[str, str]]) -> tuple[list[dict], list[str]]:
    """Wavefunction files each &DFT reads, from the prefixed --echo lines."""
    files, warnings, stack, project, dft = [], [], [], None, None
    for flag, line in echo:
        words = line.split()
        if not words:
            continue
        head = words[0].upper()
        if head == "&END":
            if stack and stack[-1] == "DFT" and dft is not None:
                guess = dft["guess"]
                if dft["wfn"] is not None and guess in _READS_WFN:
                    files.append({"keyword": "WFN_RESTART_FILE_NAME", "path": dft["wfn"][0],
                                  "exists": dft["wfn"][1]})
                elif guess in _READS_WFN and project is not None:
                    files.append({"keyword": "WFN_RESTART_FILE_NAME (unset: CP2K's default name)",
                                  "path": project[0], "exists": project[1]})
                elif dft["wfn"] is not None:
                    warnings.append(f"WFN_RESTART_FILE_NAME {dft['wfn'][0]} is set but SCF_GUESS is "
                                    f"{guess}: CP2K will not read it")
                dft = None
            stack = stack[:-1]
        elif head.startswith("&"):
            stack.append(head[1:])
            if stack[-1] == "DFT":
                dft = {"wfn": None, "guess": "ATOMIC"}
        elif head == "PROJECT_NAME" and stack == ["GLOBAL"]:
            name = line.split(None, 1)[1].strip().strip('"') if len(words) > 1 else ""
            project = (f"{name}-RESTART.wfn", flag == "1")
        elif dft is not None and stack and stack[-1] == "DFT" and head == "WFN_RESTART_FILE_NAME":
            path = line.split(None, 1)[1].strip().strip('"') if len(words) > 1 else ""
            if path:
                dft["wfn"] = (path, flag == "1")
        elif dft is not None and stack[-2:] == ["DFT", "SCF"] and head == "SCF_GUESS" and len(words) > 1:
            dft["guess"] = words[1].upper()
    return files, warnings


def check_input(facility: str, input_path: str, build_id: str | None = None) -> dict:
    pack_values(facility)
    config.require_facility_configuration(facility)
    build = resolve_build(facility, build_id)
    folder, name = (input_path.rsplit("/", 1) if "/" in input_path else (".", input_path))
    run = "OMP_NUM_THREADS=1 timeout 60 cp2k.psmp"
    body = (f"cd {_home_path(folder or '/')} && source {_home_path(build['env_script'])} >/dev/null 2>&1 "
            f"&& {run} --check -i {shlex.quote(name)}; rc=$?; "
            f"if [ $rc -eq 0 ]; then {{ {run} --echo -i {shlex.quote(name)} 2>&1; "
            f"echo {_ECHO}_EXIT=$?; }} | {_ECHO_LOOP}; fi; exit $rc")
    code, out = _run_with_exit(facility, body)
    lines = [l for l in out if not l.startswith(_ECHO + " ")]
    # (exists flag "1"/"0"/"-", echoed line)
    echo = [((l.split(" ", 2) + [""])[1], (l.split(" ", 2) + [""])[2]) for l in out
            if l.startswith(_ECHO + " ")]
    # CP2K reports a bad input inside a box of *** lines ([ABORT] and the
    # message); a missing env or binary shows up as a shell error line
    errors = [l.strip(" *") for l in lines
              if (l.lstrip().startswith("***") or "ERROR" in l or "command not found" in l
                  or "No such file" in l) and l.strip(" *")]
    # a real [ABORT] box is " *"-bordered lines between star rules: its
    # message, unwrapped, goes first
    errors = [f"[ABORT] {' '.join(_box_text(box))}" for box in _boxes(lines)
              if any("[ABORT]" in l for l in box)] + errors

    # the files the input reads at run time, relative to its own folder
    files, warnings = [], []
    missing_restart = _abort_missing_file(lines) if code != 0 else None
    if missing_restart:
        files.append({"keyword": "EXT_RESTART/RESTART_FILE_NAME", "path": missing_restart,
                      "exists": False})
        warnings.append(f"EXT_RESTART RESTART_FILE_NAME {missing_restart} does not exist: CP2K "
                        "stops before the run (set RESTART 0 for a fresh start, or point it at the "
                        "run's -1.restart); the wavefunction file was not checked")
    elif code == 0:
        echo_exit = next((e[1].split("=", 1)[1] for e in echo
                          if e[1].startswith(f"{_ECHO}_EXIT=")), None)
        echoed = [e for e in echo if not e[1].startswith(f"{_ECHO}_EXIT=")]
        if echo_exit != "0":
            warnings.append(f"cp2k.psmp --echo failed (exit {echo_exit}): the restart and "
                            "wavefunction files were not checked")
        else:
            restart = _ext_restart_read([e[1] for e in echoed])
            if restart:
                files.append({"keyword": "EXT_RESTART/RESTART_FILE_NAME", "path": restart,
                              "exists": True})
            wfn, notes = _wfn_files(echoed)
            files += wfn
            warnings += notes
            warnings += [f"WFN_RESTART_FILE_NAME {f['path']} does not exist (from {folder}): CP2K "
                         "would start from the atomic guess, and a hybrid run then costs far more"
                         for f in wfn if not f["exists"]]
    return {"ok": code == 0, "build_id": build["build_id"], "exit_code": code,
            "timed_out": code == 124, "errors": errors[:20], "files": files,
            "warnings": warnings, "tail": lines[-40:]}


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


KIND_FAMILIES = ("molopt", "uzh-pbe", "uzh-scan", "uzh-pbe0")
KIND_DEFAULT_BASIS = {"molopt": "DZVP-MOLOPT-SR-GTH", "uzh-pbe": "DZVP-MOLOPT-PBE-GTH",
                      "uzh-scan": "DZVP-MOLOPT-SCAN-GTH", "uzh-pbe0": "DZVP-MOLOPT-PBE0-GTH"}


def kinds(facility: str, elements: list[str], basis: str | None = None,
          build_id: str | None = None, family: str = "molopt",
          aux_basis: str | None = None) -> dict:
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
    if family not in KIND_FAMILIES:
        raise ValueError(f"family is one of {list(KIND_FAMILIES)}")
    basis = basis or KIND_DEFAULT_BASIS[family]
    for word in (basis, aux_basis):
        if word is not None and not re.fullmatch(r"[A-Za-z0-9._-]+", word):
            raise ValueError(f"not a basis set name: {word!r}")
    if aux_basis and family == "molopt":
        raise ValueError('aux_basis comes from BASIS_ADMM_UZH: use a "uzh-*" family')
    flags = ["--family", family] + (["--aux", aux_basis] if aux_basis else [])
    args = " ".join([*flags, _home_path(build["data_dir"]), shlex.quote(basis), *elements])
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


# metric per run type: an MD step is timed per step, the rest as whole runs
_METRIC = {"aimd_step": "s_per_step", "single_point": "wall_s", "geo_opt": "wall_s",
           "cell_opt": "wall_s"}


def _run_reason(run: dict) -> str | None:
    """Why a parsed run is not a usable timing, or None if it is."""
    if not run["done"]:
        return "did not finish (no PROGRAM ENDED: failed, killed or timed out)"
    if run["scf_nc"]:
        return f"SCF not converged in {run['scf_nc']} force evaluation(s) after the first"
    if run["dE_max"] is None:
        return "no energies to compare with the other layouts"
    if not run["ok"]:
        return (f"energy or forces differ from the other layouts (dE_max {run['dE_max']:.1e} Ha"
                + (f", dF_max {run['dF_max']:.1e} Ha/bohr)" if run["dF_max"] is not None else ")"))
    if not run["s_per_step"]:
        return "no timed steps"
    return None


def bench_records(facility: str, system: dict, method: dict, run_type: str,
                  parsed: dict | None = None, stage_dirs: list[str] | None = None,
                  input_name: str | None = None, nodes: int = 1, build_id: str | None = None,
                  job_ids: list[str] | None = None, date: str | None = None,
                  node_type: str | None = None, tag: str | None = None) -> dict:
    vals = pack_values(facility)
    if run_type not in _METRIC:
        raise ValueError(f"run_type is one of {list(_METRIC)}: {run_type!r}")
    if (parsed is None) == (stage_dirs is None):
        raise ValueError("give either parsed (a cp2k_bench_parse result) or stage_dirs")
    nodes = int(nodes)
    if nodes < 1:
        raise ValueError("nodes must be at least 1")
    if tag is not None and not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", tag):
        raise ValueError(f"tag is a short lower-case id part, e.g. lda or ot-fullall: {tag!r}")
    if parsed is None:
        config.require_facility_configuration(facility)
    build = resolve_build(facility, build_id)
    if parsed is None:
        parsed = bench_parse(facility, stage_dirs)
    date = date or datetime.date.today().isoformat()

    runs = parsed.get("runs") or []
    if input_name:
        name = input_name.rsplit("/", 1)[-1].removesuffix(".inp")
        runs = [r for r in runs if r["input"] == name]
    inputs = sorted({r["input"] for r in runs})
    if len(inputs) != 1:
        raise ValueError(f"the runs are of {len(inputs)} inputs {inputs}: give input_name, the one "
                         "the system and method describe" if inputs else
                         "no runs to record" + (f" of {input_name}" if input_name else ""))
    jobs = {r["job"].split("/")[0] for r in runs}
    if any(j.startswith("dry") for j in jobs):
        raise ValueError("these are dry-run results (a stub copies one cp2k.out): nothing to record")
    if any(r["R"] % nodes for r in runs):
        raise ValueError(f"a run's ranks do not divide over {nodes} nodes: is nodes right?")
    want_sp = run_type != "aimd_step"
    if any(r["done"] and r["sp"] != want_sp for r in runs):
        raise ValueError(f"run_type {run_type} but the runs are "
                         f"{'single points' if not want_sp else 'MD'}: is run_type right?")

    partition = vals["CP2K_PARTITION"]
    cores = int(vals["CP2K_CORES_PER_NODE"])
    if node_type is None:
        # the label the facility's records already use, so queries line up
        known = records.query(facility, "benchmarks", {"partition": partition}, ["node_type"])
        node_type = known[0]["node_type"] if known else f"{partition}, {cores} cores"

    layouts = {}
    for r in runs:
        layouts.setdefault((r["R"], r["T"]), []).append(r)
    usable = {k: [r for r in reps if _run_reason(r) is None] for k, reps in layouts.items()}
    mean = {k: sum(r["s_per_step"] for r in reps) / len(reps) for k, reps in usable.items() if reps}
    pure_mpi = next((v for (R, T), v in mean.items() if T == 1 and R * T == nodes * cores), None)
    label = re.sub(r"[^a-z0-9]", "", system.get("label", "").lower()) if isinstance(system, dict) else ""
    short = run_type.replace("_", "")

    bench, prov = [], []
    for (R, T), reps in sorted(layouts.items()):
        rpn = R // nodes
        rid = f"{facility}-cp2k-{date}-{label}-{short}-{nodes}n-{rpn}x{T}" + (f"-{tag}" if tag else "")
        ok = (R, T) in mean
        value = mean[(R, T)] if ok else next((r["s_per_step"] for r in reps if r["s_per_step"]), 0.0)
        extra = {}
        first = [r["first_s"] for r in usable[(R, T)] if r["first_s"] is not None]
        if ok and not want_sp and first:
            extra["first_step_s"] = round(sum(first) / len(first), 3)
        if ok and pure_mpi and R * T == nodes * cores:
            extra["speedup_vs_pure_mpi"] = round(pure_mpi / value, 3)
        rec = {"id": rid, "facility": facility, "partition": partition, "node_type": node_type,
               "code": "cp2k", "version": str(build["version"]), "build_id": build["build_id"],
               "system": system, "method": method, "run_type": run_type, "nodes": nodes,
               "ranks_per_node": rpn, "threads_per_rank": T,
               "metric": {"name": _METRIC[run_type], "value": round(float(value), 4)},
               **({"extra": extra} if extra else {}),
               "job_id": None, "date": date, "valid": ok}
        if not ok:
            rec["reason"] = _run_reason(reps[0])
        if len(reps) > 1:
            rec["notes"] = (f"{len(reps)} repeats ({len(usable[(R, T)])} usable): "
                            + ", ".join(f"{r['s_per_step']:.3f}" if r["s_per_step"] else "none"
                                        for r in sorted(reps, key=lambda r: r["rep"])))
        bench.append(rec)
        ids = sorted(job_ids) if job_ids else sorted({r["job"].split("/")[0] for r in reps})
        prov.append({"id": rid, "benchmark_id": rid, "job_ids": [str(j) for j in ids]})

    # every record valid before any is written: no half-recorded benchmark
    errs = [e for rec in bench for e in records.errors(facility, "benchmarks", rec)]
    errs += [e for p in prov for e in records.errors(facility, "benchmarks_provenance", p)]
    ids = [r["id"] for r in bench]
    errs += [f"two layouts would share the id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    if errs:
        raise records.RecordsError("nothing appended:\n  " + "\n  ".join(errs)
                                   + ("\n(an id that exists: pass a tag, or supersede the old record)"
                                      if any("already exists" in e for e in errs) else ""))
    for rec in bench:
        records.append(facility, "benchmarks", rec)
    for p in prov:
        records.append(facility, "benchmarks_provenance", p)
    return {"appended": ids, "valid": [r["id"] for r in bench if r["valid"]],
            "invalid": {r["id"]: r["reason"] for r in bench if not r["valid"]},
            "provenance": [p["id"] for p in prov], "records": bench,
            "check_errors": records.check(facility)["errors"]}
