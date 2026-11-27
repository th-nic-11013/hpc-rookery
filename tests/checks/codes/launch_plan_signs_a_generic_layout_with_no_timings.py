import copy
import json
import os
import pathlib
import tempfile
from unittest.mock import patch

LABEL = 'launch_plan with no timings on a facility: a generic, flagged, signed layout (step 5b L)'


def launch_plan_signs_a_generic_layout_with_no_timings():
    """On PARAM Seva (no cp2k benchmark records) launch_plan refused with
    "No cp2k timings at all", so with the submit guard on (G95) there was no
    signed spec and not even the benchmark that fills the records could run
    (kairo Seva probe). Now it returns a generic layout (every core, 4/2/1
    threads per rank, one node), basis.match "none" with
    basis.default_layout "generic", a loud warning and no estimate, signed
    like any plan; the guard accepts it within budget. Recorded timings
    still win: the shipped HOKUSAI records give "exact" and "similar"
    plans with no default_layout."""
    from hpc_codes import records, server
    from hpc_mcp import hpc_server

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
        shipped = records.facility_dir
        empty = home / "no-shipped-records"
        with patch.dict(os.environ, env, clear=True):
            server.records_append("hokusai", "software_registry", {
                "build_id": "cp2k-2026.2-psmp-cpu-gcc", "facility": "hokusai", "code": "cp2k",
                "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"}, "toolchain": "gcc",
                "install_path": "software/cp2k", "env_script": "~/software/cp2k/cp2k_env.sh",
                "test_status": {"level": "none", "result": "pass"}, "default": True,
                "date": "2026-11-04"})
            h2o = {"atoms": 384, "label": "H2O-128"}

            # --- a new facility: nothing recorded at all ------------------------------------
            with patch.object(records, "facility_dir", lambda f: empty):
                assert not records.query("hokusai", "benchmarks", {"code": "cp2k"})
                p = server.launch_plan("hokusai", "cp2k", "single_point", h2o)
            b = p["basis"]
            assert b["match"] == "none" and b["default_layout"] == "generic", b
            assert b["record_ids"] == [] and "generic default" in b["why"] and "not a measured" in b["why"], b
            assert p["layout"] == {"nodes": 1, "ranks_per_node": 28, "threads_per_rank": 4}, p["layout"]
            assert p["estimate"] is None, p["estimate"]
            assert any(w.startswith("NO cp2k TIMINGS ON hokusai") and "benchmark" in w
                       for w in p["warnings"]), p["warnings"]
            js = p["job_spec"]
            assert js["resources"] == {"node_count": 1, "processes_per_node": 28,
                                       "cpu_cores_per_process": 4}, js["resources"]
            assert js.get("plan_signature", "").startswith("g95.v1."), "the generic plan is not signed"

            # the guard takes it, within budget, for a short first benchmark
            spec = {**copy.deepcopy(js), "name": "bench", "executable": "cp2k.psmp -i bench.inp",
                    "directory": "~/agent/work/bench"}
            spec["attributes"] = {**spec.get("attributes", {}), "duration": "00:30:00"}
            v = hpc_server.submit_job("hokusai", hpc_server.JobSpec.model_validate(spec), dry_run=True)
            assert v["verdict"] == "accept" and v["refusals"] == [], v
            long = copy.deepcopy(spec)
            long["attributes"]["duration"] = "01:00:00"           # 112 core-h > 100
            v = hpc_server.submit_job("hokusai", hpc_server.JobSpec.model_validate(long), dry_run=True)
            assert v["verdict"] == "refuse" and "over the budget of 100 core-h" in v["refusals"][0], v

            # a pack default layout still beats the generic one (and is flagged "pack")
            from hpc_codes import packs
            real_values = packs.pack_values

            def with_default(facility, code):
                return {**real_values(facility, code), "CP2K_DEFAULT_LAYOUT": "56x2"}
            with patch.object(records, "facility_dir", lambda f: empty), \
                    patch.object(packs, "pack_values", with_default):
                d = server.launch_plan("hokusai", "cp2k", "single_point", h2o)
            assert d["basis"]["default_layout"] == "pack" and d["layout"]["ranks_per_node"] == 56, d["basis"]

            # --- recorded timings still win (the shipped HOKUSAI records) -------------------
            assert records.facility_dir is shipped
            exact = server.launch_plan("hokusai", "cp2k", "single_point", {"atoms": 456, "label": "UiO-66-conv"})
            assert exact["basis"]["match"] == "exact" and "default_layout" not in exact["basis"], exact["basis"]
            assert exact["estimate"] is not None
            near = server.launch_plan("hokusai", "cp2k", "single_point", {"atoms": 470, "label": "UiO-66-like"})
            assert near["basis"]["match"] == "similar" and "default_layout" not in near["basis"], near["basis"]
            far = server.launch_plan("hokusai", "cp2k", "single_point", {"atoms": 5, "label": "tiny"})
            assert far["basis"]["match"] == "none" and "default_layout" not in far["basis"], far["basis"]
            assert "recorded cases" in far["basis"]["why"], far["basis"]
            for plan in (exact, near, far):
                assert plan["job_spec"].get("plan_signature", "").startswith("g95.v1.")
