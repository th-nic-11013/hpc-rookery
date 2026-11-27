import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch
from checks._common import ROOT, local_run_command

LABEL = 'cp2k_bench_input scripts=true writes the whole benchmark folder; the plan runs it (step 6 R3)'

STUB = """#!/bin/bash
# stub launcher: logs its arguments, copies a finished cp2k.out
printf '%s\\n' "$(basename "$0") $*" >> "{log}"
[ "$(basename "$0")" = "{forbid}" ] && exit 99
cp "{ref}" cp2k.out
"""


def _shims(tmp: pathlib.Path) -> pathlib.Path:
    """Login-node GNU tools the scripts use, on a macOS test machine."""
    shims = tmp / "shims"
    shims.mkdir()
    if sys.platform == "darwin":
        (shims / "sed").write_text('#!/bin/bash\n[ "$1" = -i ] && { shift; exec /usr/bin/sed -i "" "$@"; }\n'
                                   'exec /usr/bin/sed "$@"\n')
        (shims / "timeout").write_text('#!/bin/bash\nwhile [[ $1 = -* ]]; do shift; done\nshift\nexec "$@"\n')
    (shims / "lscpu").write_text("#!/bin/bash\necho 'NUMA node0 CPU(s): 0-111'\n")
    for f in shims.iterdir():
        f.chmod(0o755)
    return shims


def _run_job(folder: pathlib.Path, env: dict, tmp: pathlib.Path, shims: pathlib.Path, ref: pathlib.Path,
             launcher: str, forbid: str) -> tuple[int, str, list[str]]:
    """bench_job.sh as the job runs it: from a copy of the folder, with the
    spec's environment and a fake build whose env script puts the stub
    launcher and cp2k.psmp on PATH."""
    run = tmp / f"job-{len(list(tmp.glob('job-*')))}"
    shutil.copytree(folder, run)
    fake = run.parent / (run.name + "-build")
    fake.mkdir()
    log = fake / "launch.log"
    for name in {launcher, forbid}:
        (fake / name).write_text(STUB.format(log=log, forbid=forbid, ref=ref))
    (fake / "cp2k.psmp").write_text("#!/bin/bash\nexit 1\n")
    for f in fake.iterdir():
        f.chmod(0o755)
    (fake / "env.sh").write_text(f"export PATH={fake}:$PATH\n")
    full = {"PATH": f"{shims}:{os.environ['PATH']}", "HOME": str(tmp), "SLURM_JOB_ID": "4242",
            **env, "CP2K_ENV": str(fake / "env.sh")}
    out = subprocess.run(["bash", "bench_job.sh"], cwd=run, env=full, capture_output=True, text=True,
                         timeout=300)
    calls = log.read_text().splitlines() if log.exists() else []
    return out.returncode, out.stdout + out.stderr, calls


def cp2k_bench_input_writes_the_benchmark_folder():
    """S0 stages the benchmark with one fs_upload: cp2k_bench_input
    (scripts=true) writes the inputs into out_dir/inputs/ and the scripts,
    rendered with the facility's pack values (no {{...}} left), into
    out_dir, and returns submit_fields (executable, SMOKE_INPUT,
    SCAN_INPUT, VALIDATE_INPUT, CP2K_ENV). End to end, offline: the
    rendered bench_job.sh, run with launch_plan(run_type="benchmark")'s
    environment plus those fields, runs the smoke input in the smoke
    layouts and the scan input in the scan layouts, skips stage 3 and
    keeps the plan's BENCH_LIMIT_S; the launcher is the pack's
    CP2K_BENCH_LAUNCH, HOKUSAI's srun line as before and mpirun for a
    facility that says so (srun never called); cp2k_bench_dryrun passes
    on both folders with the same settings."""
    from hpc_codes import launch, server
    from hpc_codes.cp2k import tools
    real_pack_values = tools.pack_values
    fx = ROOT / "tests" / "fixtures" / "structures"
    data = ROOT / "tests" / "fixtures" / "cp2k_data"
    ref = ROOT / "tests" / "fixtures" / "cp2k_bench" / "stage1" / "H2O-32_8x14_r1" / "cp2k.out"
    hokusai = {**tools.pack_values("hokusai"), "CP2K_PYTHON": sys.executable}
    mpirun = {**hokusai, "CP2K_BENCH_LAUNCH": "mpirun --map-by ppr:{RPN}:node:PE={T} --bind-to core"}
    values = {"v": hokusai}
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(tools, "run_command", local_run_command), \
            patch.object(tools, "pack_values", lambda f, code="cp2k": (
                values["v"] if f == "hokusai" else real_pack_values(f, code))), \
            patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp, "HOKUSAI_HOST": "localhost",
                                    "HPC_SUBMIT_GUARD": ""}):
        tmp = pathlib.Path(tmp)
        shims = _shims(tmp)
        server.records_append("hokusai", "software_registry", {
            "build_id": "cp2k-2026.2-fake", "facility": "hokusai", "code": "cp2k", "version": "2026.2",
            "variant": {"exe": "psmp", "target": "cpu"}, "toolchain": "fake", "install_path": str(tmp),
            "env_script": "~/software/cp2k/cp2k_env.sh", "data_dir": str(data),
            "test_status": {"level": "none", "result": "pass"}, "default": True, "date": "2026-11-04"})

        out = tmp / "bench"
        r = server.cp2k_bench_input("hokusai", str(fx / "clean.xyz"), "sys", str(out), scripts=True)
        assert r["inputs"] == [str(out / "inputs" / "sys.inp"), str(out / "inputs" / "sys-smoke.inp")], r
        names = {"bench_job.sh", "run_layouts.sh", "stage1_smoke.sh", "stage2_scan.sh", "stage3_validate.sh",
                 "dryrun.sh", "parse_bench.py"}
        assert {pathlib.Path(p).name for p in r["scripts"]} == names, r["scripts"]
        assert {p.name for p in out.iterdir()} == names | {"inputs"}, list(out.iterdir())
        for name in names:
            text = (out / name).read_text()
            assert not re.search(r"\{\{\w+\}\}", text), (name, re.findall(r"\{\{\w+\}\}", text))
            assert os.access(out / name, os.X_OK), name
        assert "BENCH_LAUNCH='srun --mpi=pmix --ntasks={R} --cpus-per-task={T} {NODEOPT} " \
               "--cpu-bind=verbose,cores --distribution=block:block'" in (out / "run_layouts.sh").read_text()
        assert r["submit_fields"] == {"executable": "bash bench_job.sh", "environment": {
            "SMOKE_INPUT": "sys-smoke.inp", "SCAN_INPUT": "sys.inp", "VALIDATE_INPUT": "none",
            "CP2K_ENV": "~/software/cp2k/cp2k_env.sh"}}, r["submit_fields"]
        try:
            server.cp2k_bench_input("hokusai", str(fx / "clean.xyz"), "sys", str(out), scripts=True)
        except ValueError as e:
            assert "overwrite" in str(e), e
        else:
            raise AssertionError("scripts=true overwrote an existing benchmark folder")

        plan = launch.plan("hokusai", "cp2k", "benchmark", {"atoms": 6, "label": "sys"})
        env = {**plan["job_spec"]["environment"], **r["submit_fields"]["environment"]}
        smoke = plan["benchmark"]["smoke_layouts"]
        scan = plan["benchmark"]["scan_layouts"]

        # --- HOKUSAI: bench_job.sh with the plan's and bench_input's settings
        code, log, calls = _run_job(out, env, tmp, shims, ref, "srun", "mpirun")
        assert code == 0 and "all stages passed" in log, log[-3000:]
        assert "stage 3 skipped (VALIDATE_INPUT=none)" in log, log[-3000:]
        want = [f"srun --mpi=pmix --ntasks={L.split('x')[0]} --cpus-per-task={L.split('x')[1]} "
                "--cpu-bind=verbose,cores --distribution=block:block cp2k.psmp -i sys-smoke.inp -o cp2k.out"
                for L in smoke]
        want += [f"srun --mpi=pmix --ntasks={L.split('x')[0]} --cpus-per-task={L.split('x')[1]} "
                 "--cpu-bind=verbose,cores --distribution=block:block cp2k.psmp -i sys.inp -o cp2k.out"
                 for L in scan]
        assert calls == want, (calls, want)
        # the plan's BENCH_LIMIT_S is what the time guard reads (bench_job.sh does not overwrite it)
        code, log, calls = _run_job(out, {**env, "BENCH_LIMIT_S": "100"}, tmp, shims, ref, "srun", "mpirun")
        assert code == 1 and "NOT STARTED" in log and calls == [], log[-2000:]

        # cp2k_bench_dryrun takes the same settings on the uploaded-as-is folder
        with patch.dict(os.environ, {"HOME": str(tmp), "PATH": f"{shims}:{os.environ['PATH']}"}):
            d = server.cp2k_bench_dryrun("hokusai", str(out), str(ref), env, keep_scratch=True)
        assert d["ok"], json.dumps(d, indent=1)[-3000:]
        status = pathlib.Path(d["cases"][0]["scratch"]) / "bench" / "runs" / "dry-pass"
        rows = [l.split("\t")[:3] for s in ("stage1", "stage2")
                for l in (status / s / "status.tsv").read_text().splitlines()[1:]]
        assert rows == [["sys-smoke", *L.split("x")] for L in smoke] + [["sys", *L.split("x")] for L in scan], rows
        assert not (status / "stage3").exists() or not list((status / "stage3").glob("sys*")), status

        # --- a facility whose launcher is mpirun (PARAM Seva's Open MPI build)
        values["v"] = mpirun
        out2 = tmp / "bench-mpirun"
        r2 = server.cp2k_bench_input("hokusai", str(fx / "clean.xyz"), "sys", str(out2), scripts=True)
        layouts_sh = (out2 / "run_layouts.sh").read_text()
        assert "BENCH_LAUNCH='mpirun --map-by ppr:{RPN}:node:PE={T} --bind-to core'" in layouts_sh
        assert "srun " not in layouts_sh.split("BENCH_LAUNCH=", 1)[1].split("\n", 1)[0]
        code, log, calls = _run_job(out2, {**env, **r2["submit_fields"]["environment"]}, tmp, shims, ref,
                                    "mpirun", "srun")
        assert code == 0 and "all stages passed" in log, log[-3000:]
        assert calls[0] == f"mpirun --map-by ppr:{smoke[0].split('x')[0]}:node:PE={smoke[0].split('x')[1]} " \
                           "--bind-to core cp2k.psmp -i sys-smoke.inp -o cp2k.out", calls
        assert len(calls) == len(smoke) + len(scan) and not any(c.startswith("srun") for c in calls), calls
        with patch.dict(os.environ, {"HOME": str(tmp), "PATH": f"{shims}:{os.environ['PATH']}"}):
            d = server.cp2k_bench_dryrun("hokusai", str(out2), str(ref), env)
        assert d["ok"], json.dumps(d, indent=1)[-3000:]

        # a launcher that would break the scripts' quoting is refused before anything is written
        values["v"] = {**hokusai, "CP2K_BENCH_LAUNCH": "srun --export='ALL'"}
        try:
            server.cp2k_bench_input("hokusai", str(fx / "clean.xyz"), "q", str(tmp / "q"), scripts=True)
        except ValueError as e:
            assert "single" in str(e), e
        else:
            raise AssertionError("a quoted launcher was rendered")
        assert not (tmp / "q").exists()
        # the skill's copies are these same files
        for name in names - {"parse_bench.py"}:
            assert (ROOT / "codes" / "cp2k" / "benchmark" / "scripts" / name).resolve() == \
                (ROOT / "hpc_codes" / "cp2k" / "bench_scripts" / name).resolve(), name
