import copy
import json
import os
import pathlib
import tempfile
from unittest.mock import patch

LABEL = 'submit_job dry_run: the submit guard verdict and script, nothing submitted (step 5b E)'


def submit_job_dry_run_reports_the_guard_verdict():
    """Nobody could test the submit guard's refusals (G95) without risking
    a real submit (kairo step 5). submit_job(dry_run=True) runs the same
    checks and the script render and returns the verdict: every refusal
    path here (unsigned, changed spec, forged signature, over budget, guard
    off) comes back as data, refusals[0] is the error a real submit raises,
    and the scheduler stub is never called. The key and config live in a
    temporary HOME."""
    from hpc_codes import server
    from hpc_mcp import hpc_server
    from facilities.registry import get_backend

    backend = get_backend("hokusai")
    submitted = []

    def fake_submit(spec):
        submitted.append(spec)
        return {"job_id": str(len(submitted))}

    with tempfile.TemporaryDirectory() as tmp:
        home = pathlib.Path(tmp)
        cfg = home / "hokusai.json"
        cfg.write_text(json.dumps({"ssh": {"host": "hokusai.example"},
                                   "defaults": {"account": "rb000000"},
                                   "submit_guard": {"budget_core_hours": 500}}))
        env = {k: v for k, v in os.environ.items()
               if k not in ("HOKUSAI_HOST", "HPC_SUBMIT_GUARD", "HPC_RECORDS_ROOT")}
        env.update(HOME=tmp, HOKUSAI_CONFIG=str(cfg), HPC_RECORDS_USER_DIR=str(home / "records"))
        with patch.dict(os.environ, env, clear=True), patch.object(backend, "submit", fake_submit):
            server.records_append("hokusai", "software_registry", {
                "build_id": "cp2k-2026.2-psmp-cpu-gcc", "facility": "hokusai", "code": "cp2k",
                "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"}, "toolchain": "gcc",
                "install_path": "software/cp2k", "env_script": "~/software/cp2k/cp2k_env.sh",
                "test_status": {"level": "none", "result": "pass"}, "default": True,
                "date": "2026-11-04"})
            uio = {"atoms": 456, "label": "UiO-66-conv"}
            hand = {"name": "t", "executable": "bash run.sh", "directory": "~/agent/work/t",
                    "resources": {"node_count": 1, "processes_per_node": 28, "cpu_cores_per_process": 4},
                    "attributes": {"duration": "01:00:00", "queue_name": "mpc"}}

            def dry(spec: dict) -> dict:
                out = hpc_server.submit_job("hokusai", hpc_server.JobSpec.model_validate(copy.deepcopy(spec)),
                                            dry_run=True)
                assert out["dry_run"] is True and out["submitted"] is False, out
                assert not submitted, f"a dry run reached the scheduler: {submitted}"
                return out

            def real_error(spec: dict) -> str:
                try:
                    hpc_server.submit_job("hokusai", hpc_server.JobSpec.model_validate(copy.deepcopy(spec)))
                except ValueError as e:
                    return str(e)
                raise AssertionError(f"a real submit accepted {spec}")

            # --- guard off: a hand-written spec is accepted, the budget is information only
            off = dry({**hand, "attributes": {**hand["attributes"], "duration": "10:00:00"}})
            assert off["guard"] == "off" and off["verdict"] == "accept" and off["refusals"] == [], off
            assert off["worst_case"]["core_hours"] == 1120 and off["budget_core_hours"] == 500, off
            assert "bash run.sh" in off["script"] and off["account"] == "rb000000", off["script"]
            assert not (home / ".hpc-agent" / "submit_guard.key").exists(), "the key was made with the guard off"

            os.environ["HPC_SUBMIT_GUARD"] = "1"

            # --- unsigned: refused, and the script is still rendered for inspection
            v = dry(hand)
            assert v["guard"] == "on" and v["verdict"] == "refuse", v
            assert len(v["refusals"]) == 1 and "no plan_signature" in v["refusals"][0], v
            assert v["refusals"][0] == real_error(hand), "the dry run's refusal is not the real one"
            assert v["script"] and "bash run.sh" in v["script"], v
            submitted.clear()

            # --- signed and unchanged: accepted
            js = server.launch_plan("hokusai", "cp2k", "single_point", uio, nodes=1)["job_spec"]
            good = {**copy.deepcopy(js), "name": "sp", "executable": "cp2k.psmp -i sp.inp",
                    "directory": "~/agent/work/sp"}
            good["attributes"] = {**good.get("attributes", {}), "duration": "00:30:00"}
            ok = dry(good)
            assert ok["verdict"] == "accept" and ok["refusals"] == [], ok
            assert ok["worst_case"]["core_hours"] == 56 and ok["budget_core_hours"] == 500, ok
            assert "cp2k.psmp -i sp.inp" in ok["script"], ok["script"]

            # --- changed spec: refused, naming the field and both values
            more = copy.deepcopy(good)
            more["resources"]["node_count"] = 4
            v = dry(more)
            assert v["verdict"] == "refuse" and "spec.resources.node_count is 4" in v["refusals"][0] \
                and "the plan signed 1" in v["refusals"][0], v
            assert v["refusals"][0] == real_error(more)
            submitted.clear()

            # --- forged signature: refused
            v = dry({**good, "plan_signature": good["plan_signature"][:-4] + "0000"})
            assert v["verdict"] == "refuse" and "not valid" in v["refusals"][0], v

            # --- over budget: refused with the numbers
            over = copy.deepcopy(good)
            over["attributes"]["duration"] = "05:00:00"          # 5 h x 112 cores = 560 > 500
            v = dry(over)
            assert v["verdict"] == "refuse" and len(v["refusals"]) == 1, v
            assert "over the budget of 500 core-h" in v["refusals"][0] and "560 core-h" in v["refusals"][0], v
            assert v["worst_case"]["core_hours"] == 560, v
            assert v["refusals"][0] == real_error(over)
            submitted.clear()

            # --- unsigned and over budget: both refusals, the signature's first (as a real submit)
            v = dry({**hand, "attributes": {**hand["attributes"], "duration": "05:00:00"}})
            assert len(v["refusals"]) == 2, v
            assert "no plan_signature" in v["refusals"][0] and "over the budget" in v["refusals"][1], v

            # --- off again: the over-budget spec is accepted, as a real submit would
            os.environ["HPC_SUBMIT_GUARD"] = "0"
            v = dry(over)
            assert v["guard"] == "off" and v["verdict"] == "accept", v
            assert v["worst_case"]["core_hours"] == 560 and v["budget_core_hours"] == 500, v
