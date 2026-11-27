import copy
import json
import os
import pathlib
import re
import tempfile
from unittest.mock import patch
from checks._common import ROOT

LABEL = 'launch_plan run_type="benchmark": the signed one-node benchmark job (step 6 R1)'


def launch_plan_plans_the_benchmark_job():
    """S0 plans the layout-scan benchmark with launch_plan, so a guarded
    harness (G95) can submit it: one exclusive node, every core one process
    of one thread, no launcher (bench_job.sh launches each run), the
    walltime from the stage timeouts and the pack's smoke and scan layouts
    (HOKUSAI: 2 x (120+90) + 6 x (240+90) + 120 s = 00:42:00), and the
    settings bench_job.sh reads in benchmark.environment and
    job_spec.environment. submit_job(dry_run) accepts it with only free
    fields added (name, directory, executable, environment), refuses
    changed resources, and launch_plan refuses nodes=2."""
    from hpc_codes import launch, server
    from hpc_mcp import hpc_server

    # the timeouts the walltime is built from are the stage scripts' defaults
    scripts = ROOT / "hpc_codes" / "cp2k" / "bench_scripts"
    for name, key in (("stage1_smoke.sh", "SMOKE_TIMEOUT"), ("stage2_scan.sh", "SCAN_TIMEOUT")):
        m = re.search(r"\$\{" + key + r":-(\d+)\}", (scripts / name).read_text())
        assert m and int(m[1]) == launch.BENCH_TIMEOUTS[key], (name, key)
    assert re.search(r"-lt \$\(\( tmo \+ 90 \)\)", (scripts / "run_layouts.sh").read_text())
    assert launch.BENCH_RUN_MARGIN_S == 90
    assert "--time=0:53:00" in (scripts / "bench_job.sh").read_text() and launch.BENCH_MAX_S == 53 * 60

    with tempfile.TemporaryDirectory() as tmp:
        home = pathlib.Path(tmp)
        cfg = home / "hokusai.json"
        cfg.write_text(json.dumps({"ssh": {"host": "hokusai.example"},
                                   "defaults": {"account": "rb000000"},
                                   "submit_guard": {"budget_core_hours": 100}}))
        env = {k: v for k, v in os.environ.items()
               if k not in ("HOKUSAI_HOST", "HPC_SUBMIT_GUARD", "HPC_RECORDS_ROOT")}
        env.update(HOME=tmp, HOKUSAI_CONFIG=str(cfg), HPC_RECORDS_USER_DIR=str(home / "records"),
                   HPC_SUBMIT_GUARD="1")
        with patch.dict(os.environ, env, clear=True):
            server.records_append("hokusai", "software_registry", {
                "build_id": "cp2k-2026.2-psmp-cpu-gcc", "facility": "hokusai", "code": "cp2k",
                "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"}, "toolchain": "gcc",
                "install_path": "software/cp2k", "env_script": "~/software/cp2k/cp2k_env.sh",
                "test_status": {"level": "none", "result": "pass"}, "default": True,
                "date": "2026-11-04"})
            uio = {"atoms": 114, "label": "UiO-66-prim"}
            pbe = {"functional": "PBE", "dispersion": "bj", "cutoff_ry": 600, "basis": "DZVP-MOLOPT-SR-GTH"}
            p = server.launch_plan("hokusai", "cp2k", "benchmark", uio, pbe)
            js = p["job_spec"]
            assert js["resources"] == {"node_count": 1, "processes_per_node": 112, "cpu_cores_per_process": 1,
                                       "exclusive_node_use": True}, js["resources"]
            assert js["attributes"] == {"duration": "00:42:00", "queue_name": "mpc"}, js["attributes"]
            assert "launcher" not in js and "pre_launch" not in js, js
            assert js.get("plan_signature", "").startswith("g95.v1."), "the benchmark plan is not signed"
            assert p["layout"] == {"nodes": 1, "ranks_per_node": 112, "threads_per_rank": 1}, p["layout"]
            assert p["basis"]["run_type"] == "benchmark" and p["basis"]["match"] == "none", p["basis"]
            e = p["estimate"]
            assert e["suggested_duration"] == "00:42:00" and e["worst_case_core_hours"] == 78.4, e
            benv = p["benchmark"]["environment"]
            assert benv == {"BENCH_LIMIT_S": "2520", "SMOKE_LAYOUTS": "28x4 8x14",
                            "SCAN_LAYOUTS": "112x1 56x2 28x4 16x7 8x14 4x28",
                            "SMOKE_TIMEOUT": "120", "SCAN_TIMEOUT": "240"}, benv
            assert js["environment"] == benv, js["environment"]
            assert p["benchmark"]["runs"] == 8 and not p["warnings"], p

            # the guard takes it with only free fields added
            spec = {**copy.deepcopy(js), "name": "bench-uio66", "executable": "bash bench_job.sh",
                    "directory": "~/agent/work/live/bench"}
            spec["environment"].update(SMOKE_INPUT="UiO66-smoke.inp", SCAN_INPUT="UiO66.inp",
                                       VALIDATE_INPUT="none", CP2K_ENV="~/software/cp2k/cp2k_env.sh")
            v = hpc_server.submit_job("hokusai", hpc_server.JobSpec.model_validate(spec), dry_run=True)
            assert v["verdict"] == "accept" and v["refusals"] == [], v
            assert v["worst_case"]["core_hours"] == 78.4, v["worst_case"]
            assert "export BENCH_LIMIT_S=2520" in v["script"] and "bash bench_job.sh" in v["script"], v["script"]
            assert "#SBATCH --exclusive" in v["script"], v["script"]
            # resources stay as signed
            more = copy.deepcopy(spec)
            more["resources"]["exclusive_node_use"] = False
            v = hpc_server.submit_job("hokusai", hpc_server.JobSpec.model_validate(more), dry_run=True)
            assert v["verdict"] == "refuse" and "exclusive_node_use" in v["refusals"][0], v
            # and the budget still binds the (free) duration
            cfg.write_text(json.dumps({"ssh": {"host": "hokusai.example"},
                                       "defaults": {"account": "rb000000"},
                                       "submit_guard": {"budget_core_hours": 50}}))
            v = hpc_server.submit_job("hokusai", hpc_server.JobSpec.model_validate(spec), dry_run=True)
            assert v["verdict"] == "refuse" and "over the budget of 50 core-h" in v["refusals"][0], v

            for kw, words in (({"nodes": 2}, "runs on one node"), ({"layout": "16x7"}, "scans the pack"),
                              ({"steps": 10}, "not a benchmark setting"), ({"gpus": 1}, "CPU packs")):
                try:
                    server.launch_plan("hokusai", "cp2k", "benchmark", uio, pbe, **kw)
                except ValueError as err:
                    assert words in str(err), (kw, err)
                else:
                    raise AssertionError(f"launch_plan benchmark accepted {kw}")

            # more layouts than fit: capped at 53 min, with a warning
            from hpc_codes import packs
            real_values = packs.pack_values
            many = " ".join(["112x1", "56x2", "28x4", "16x7", "8x14", "4x28"] * 2)
            with patch.object(packs, "pack_values", lambda f, c: {**real_values(f, c), "CP2K_SCAN_LAYOUTS": many}):
                capped = server.launch_plan("hokusai", "cp2k", "benchmark", uio)
            assert capped["job_spec"]["attributes"]["duration"] == "00:53:00", capped["job_spec"]
            assert capped["benchmark"]["environment"]["BENCH_LIMIT_S"] == "3180"
            assert any("NOT STARTED" in w for w in capped["warnings"]), capped["warnings"]
            # fewer: never below the 30 min floor
            with patch.object(packs, "pack_values", lambda f, c: {**real_values(f, c), "CP2K_SCAN_LAYOUTS": "28x4",
                                                                   "CP2K_SMOKE_LAYOUTS": "28x4"}):
                short = server.launch_plan("hokusai", "cp2k", "benchmark", uio)
            assert short["job_spec"]["attributes"]["duration"] == "00:30:00", short["job_spec"]
