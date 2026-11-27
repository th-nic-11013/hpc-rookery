import copy
import json
import os
import pathlib
import tempfile
from unittest.mock import patch

LABEL = 'launch_plan layout="RxT": only a valid recorded layout, re-signed (step 6 R2)'


def launch_plan_takes_a_recorded_layout():
    """After a benchmark the planner may choose among the recorded layouts
    (cost against time): launch_plan(nodes=N, layout="RxT") plans that
    layout when this label, method and run type has a valid record at it on
    N nodes, signed for it (another signature than the default plan's), with
    that record's estimate. An unrecorded layout, one whose only record is
    invalid, another node count, a method with no records and a layout
    without nodes are refused, the refusal naming the recorded layouts."""
    from hpc_codes import server
    from hpc_mcp import hpc_server

    with tempfile.TemporaryDirectory() as tmp:
        home = pathlib.Path(tmp)
        cfg = home / "hokusai.json"
        cfg.write_text(json.dumps({"ssh": {"host": "hokusai.example"},
                                   "defaults": {"account": "rb000000"}}))
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
            rows = server.records_query("hokusai", "benchmarks", {"code": "cp2k", "system.label": "UiO-66-prim",
                                                                 "run_type": "aimd_step"})
            recorded = {f"{r['ranks_per_node']}x{r['threads_per_rank']}" for r in rows if r["valid"]}
            assert {"16x7", "8x14"} <= recorded and "4x28" not in recorded, recorded
            # an invalid record at 4x28 (a run that failed its energy check, say)
            bad = copy.deepcopy(next(r for r in rows if r["ranks_per_node"] == 8))
            for k in ("_scope",):
                bad.pop(k, None)
            bad.update(id="hokusai-cp2k-test-uio66prim-aimdstep-1n-4x28", ranks_per_node=4,
                       threads_per_rank=28, valid=False, reason="energies disagree (test)")
            server.records_append("hokusai", "benchmarks", bad)

            default = server.launch_plan("hokusai", "cp2k", "aimd_step", uio, nodes=1, steps=100)
            assert default["layout"]["ranks_per_node"] == 16, default["layout"]
            picked = server.launch_plan("hokusai", "cp2k", "aimd_step", uio, nodes=1, steps=100, layout="8x14")
            assert picked["layout"] == {"nodes": 1, "ranks_per_node": 8, "threads_per_rank": 14}, picked["layout"]
            js = picked["job_spec"]
            assert js["resources"] == {"node_count": 1, "processes_per_node": 8, "cpu_cores_per_process": 14}
            assert js["plan_signature"].startswith("g95.v1.") and \
                js["plan_signature"] != default["job_spec"]["plan_signature"]
            assert "OMP_NUM_THREADS" in js["environment"] and js["environment"]["OMP_NUM_THREADS"] == "14"
            assert picked["basis"]["match"] == "exact" and "as asked (layout=)" in picked["basis"]["why"]
            rec = next(r for r in rows if r["ranks_per_node"] == 8 and r["valid"])
            assert picked["basis"]["record_ids"] == [rec["id"]], picked["basis"]
            assert picked["estimate"]["value"] == rec["metric"]["value"], picked["estimate"]
            assert picked["estimate"]["total_wall_s"] == round(rec["metric"]["value"] * 100, 1)

            # the guard takes the re-signed spec, and not the default's resources under its signature
            spec = {**copy.deepcopy(js), "name": "md", "executable": "cp2k.psmp -i md.inp",
                    "directory": "~/agent/work/md"}
            spec["attributes"] = {**spec["attributes"], "duration": "00:30:00"}
            v = hpc_server.submit_job("hokusai", hpc_server.JobSpec.model_validate(spec), dry_run=True)
            assert v["verdict"] == "accept", v
            mixed = copy.deepcopy(spec)
            mixed["resources"] = default["job_spec"]["resources"]
            v = hpc_server.submit_job("hokusai", hpc_server.JobSpec.model_validate(mixed), dry_run=True)
            assert v["verdict"] == "refuse", v

            def refused(**kw) -> str:
                method = kw.pop("method", None)
                try:
                    server.launch_plan("hokusai", "cp2k", "aimd_step", uio, method, **kw)
                except ValueError as e:
                    return str(e)
                raise AssertionError(f"launch_plan accepted {kw}")

            msg = refused(nodes=1, layout="14x8")
            assert "no valid record" in msg and "16x7" in msg and "8x14" in msg, msg
            msg = refused(nodes=1, layout="4x28")
            assert "marked invalid" in msg and "16x7" in msg, msg
            msg = refused(nodes=2, layout="16x7")
            assert "on 2 node(s) has no valid record" in msg and "1 node(s): " in msg, msg
            msg = refused(nodes=1, layout="16x7", method={"functional": "PBE", "cutoff_ry": 600})
            assert "nothing of it is recorded" in msg and "benchmark" in msg, msg
            assert "needs nodes" in refused(layout="16x7")
            assert "16x7" in refused(nodes=1, layout="16-7")
            far = server.launch_plan  # a similar system only: refused too
            try:
                far("hokusai", "cp2k", "aimd_step", {"atoms": 120, "label": "UiO-66-like"}, nodes=1, layout="16x7")
            except ValueError as e:
                assert "only a similar system" in str(e), e
            else:
                raise AssertionError("a similar system's layout was taken")
