import json
import os
import pathlib
import sys
import tempfile
from unittest.mock import patch
from checks._common import ROOT, local_run_command

LABEL = 'cp2k_bench_dryrun runs the stage logic with a stub srun'


def cp2k_bench_dryrun_runs_the_stage_logic_with_a_stub_srun():
    """cp2k_bench_dryrun's real command: a benchmark folder rendered from
    the pack's scripts, the scrubbed smoke-stage cp2k.out as REF_OUT, all
    three cases; the benchmark folder is left as it was."""
    import re
    from hpc_codes import server
    from hpc_codes.cp2k import tools
    real_pack_values = tools.pack_values
    root = ROOT
    ref = root / "tests" / "fixtures" / "cp2k_bench" / "stage1" / "H2O-32_8x14_r1" / "cp2k.out"
    values = {**tools.pack_values("hokusai"), "CP2K_PYTHON": sys.executable}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        bench = tmp / "bench dir"     # a space: every path must be quoted
        (bench / "inputs").mkdir(parents=True)
        for f in (root / "codes" / "cp2k" / "benchmark" / "scripts").iterdir():
            text = re.sub(r"\{\{(\w+)\}\}", lambda m: str(values[m[1]]), f.read_text())
            (bench / f.name).write_text(text)
        for name in ("smoke.inp", "scan.inp", "big.inp"):
            (bench / "inputs" / name).write_text("&GLOBAL\n&END\n")
        before = sorted(p.relative_to(bench) for p in bench.rglob("*"))
        # login-node GNU tools the scripts use, on a macOS test machine
        shims = tmp / "shims"
        shims.mkdir()
        if sys.platform == "darwin":
            (shims / "sed").write_text('#!/bin/bash\n[ "$1" = -i ] && { shift; exec /usr/bin/sed -i "" "$@"; }\n'
                                       'exec /usr/bin/sed "$@"\n')
            (shims / "timeout").write_text('#!/bin/bash\nwhile [[ $1 = -* ]]; do shift; done\nshift\nexec "$@"\n')
        for f in shims.iterdir():
            f.chmod(0o755)
        settings = {"SMOKE_INPUT": "smoke.inp", "SCAN_INPUT": "scan.inp", "VALIDATE_INPUT": "big.inp",
                    "SMOKE_LAYOUTS": "28x4 8x14", "SCAN_LAYOUTS": "112x1 28x4 8x14"}
        with patch.object(tools, "run_command", local_run_command), \
                patch.object(tools, "pack_values", lambda f, code="cp2k": (
                    values if f == "hokusai" else real_pack_values(f, code))), \
                patch.dict(os.environ, {"HOME": str(tmp), "HOKUSAI_HOST": "localhost",
                                        "PATH": f"{shims}:{os.environ['PATH']}"}):
            r = server.cp2k_bench_dryrun("hokusai", str(bench), str(ref), settings)
            assert r["ok"], json.dumps(r, indent=1)[-3000:]
            case = {c["case"]: c for c in r["cases"]}
            assert case["pass"]["exit_code"] == 0 and case["pass"]["stages_run"] == [
                "stage1_smoke", "stage2_scan", "stage3_validate"], case["pass"]
            # the last scan layout not in the smoke stage: stage 2 stops, stage 3 never runs
            fl = case["fail_layout"]
            assert fl["fail_layout"] == "112x1" and fl["exit_code"] == 1, fl
            assert fl["stages_run"] == ["stage1_smoke", "stage2_scan"], fl
            assert fl["failed"] == ["scan.inp 112x1"], fl
            tl = case["time_limit"]
            assert tl["not_started"] == ["smoke 28x4 r1"] and tl["stages_run"] == ["stage1_smoke"], tl
            # a dry run that passes when it should fail is reported, not hidden
            r = server.cp2k_bench_dryrun("hokusai", str(bench), str(ref), settings,
                                         cases=["time_limit"], time_limit_s=100000)
            assert not r["ok"] and r["cases"][0]["why"], r
            kept = server.cp2k_bench_dryrun("hokusai", str(bench), str(ref), settings,
                                            cases=["pass"], keep_scratch=True)["cases"][0]["scratch"]
            assert (pathlib.Path(kept) / "bench" / "runs" / "dry-pass" / "stage2").is_dir(), kept
            left = list((tmp / "agent" / "work" / "cp2k-dryrun").iterdir())
            assert [str(p) for p in left] == [kept], left
            assert sorted(p.relative_to(bench) for p in bench.rglob("*")) == before
            for bad in ({"PATH": "/x"}, {"SLURM_JOB_ID": "1"}, {"a b": "1"}):
                try:
                    server.cp2k_bench_dryrun("hokusai", str(bench), str(ref), bad)
                except ValueError:
                    pass
                else:
                    raise AssertionError(f"cp2k_bench_dryrun accepted {bad}")
            (bench / "dryrun.sh").write_text("#!/bin/bash\nexit 0\n")
            try:
                server.cp2k_bench_dryrun("hokusai", str(bench), str(ref), settings)
            except RuntimeError as e:
                assert "stub-srun check" in str(e), e
            else:
                raise AssertionError("ran a dryrun.sh without the stub-srun check")
