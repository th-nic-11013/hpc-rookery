import copy
import json
import os
import pathlib
import tempfile
from unittest.mock import patch

LABEL = 'submit guard (G95): with HPC_SUBMIT_GUARD on, submit_job takes only a launch_plan spec within budget'


def submit_guard_takes_only_signed_launch_plan_specs():
    """kairo refused a submit_job whose spec did not come from launch_plan,
    and asked before a launch over the core-hour budget; both were kairo
    code, so another harness had neither (G95). With HPC_SUBMIT_GUARD on,
    launch_plan signs its job_spec and submit_job refuses an unsigned,
    altered or over-budget spec, naming the numbers. With it off, nothing
    changes: no signature, hand-written specs submit. The scheduler is a
    stub; the key and config live in a temporary HOME."""
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
                                   "submit_guard": {"budget_core_hours": {"rb000000": 500, "*": 100}}}))
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

            # --- off: today's behaviour --------------------------------------------------
            plan = server.launch_plan("hokusai", "cp2k", "aimd_step", uio, nodes=1)
            assert "plan_signature" not in plan["job_spec"], plan["job_spec"]
            hpc_server.submit_job("hokusai", hpc_server.JobSpec.model_validate(hand))
            assert len(submitted) == 1
            assert not (home / ".hpc-agent" / "submit_guard.key").exists(), "the key was made with the guard off"

            # --- on ----------------------------------------------------------------------
            os.environ["HPC_SUBMIT_GUARD"] = "1"

            def submit(spec: dict):
                return hpc_server.submit_job("hokusai", hpc_server.JobSpec.model_validate(spec))

            def refused(spec: dict, *needles):
                try:
                    submit(spec)
                except ValueError as e:
                    for n in needles:
                        assert n in str(e), (n, str(e))
                    return str(e)
                raise AssertionError(f"accepted: {spec}")

            refused(hand, "no plan_signature", "launch_plan")

            plan = server.launch_plan("hokusai", "cp2k", "aimd_step", uio, nodes=1)
            js = plan["job_spec"]
            sig = js.get("plan_signature", "")
            assert sig.startswith("g95.v1."), js
            key = home / ".hpc-agent" / "submit_guard.key"
            assert key.exists() and (key.stat().st_mode & 0o777) == 0o600, oct(key.stat().st_mode)

            good = {**copy.deepcopy(js), "name": "sp", "executable": "cp2k.psmp -i sp.inp",
                    "directory": "~/agent/work/sp"}
            good["attributes"] = {**good.get("attributes", {}), "duration": "02:00:00"}
            n = len(submitted)
            submit(good)
            assert len(submitted) == n + 1, "a signed launch_plan spec was refused"
            # account and a graceful-stop signal may be added
            submit({**good, "attributes": {**good["attributes"], "account": "rb000000",
                                           "custom_attributes": {"signal": "B:USR1@300"}}})

            # an altered resource is refused, naming the field and both values
            more = copy.deepcopy(good)
            more["resources"]["node_count"] = 4
            refused(more, "spec.resources.node_count is 4", "the plan signed 1")
            # an added scheduler flag that is not on the free list is refused
            flag = copy.deepcopy(good)
            flag["attributes"]["custom_attributes"] = {"nodes": "64"}
            refused(flag, "custom_attributes.nodes")
            # an added resource field (here a GPU) is refused too
            gpu = copy.deepcopy(good)
            gpu["resources"]["gpus"] = 1
            refused(gpu, "spec.resources.gpus is 1")
            # a forged or edited signature is refused
            refused({**good, "plan_signature": sig[:-4] + "0000"}, "not valid")
            refused({**good, "plan_signature": "made-up"}, "not valid")
            # a signature for another facility is refused
            from hpc_agent_core import submit_guard
            other = submit_guard.sign("rikyu", js)
            refused({**good, "plan_signature": other}, "'rikyu'")

            # --- budget: worst case = walltime x nodes x cores per node --------------------
            ppn, cpp = js["resources"]["processes_per_node"], js["resources"]["cpu_cores_per_process"]
            cores = ppn * cpp
            hours = 500 // cores + 1                       # just over the rb000000 budget of 500
            over = copy.deepcopy(good)
            over["attributes"]["duration"] = f"{hours:02d}:00:00"
            msg = refused(over, "over the budget of 500 core-h", f"{hours * cores:g} core-h",
                          f"x 1 node(s) x {cores} cores", "account rb000000")
            assert "submit_guard.budget_core_hours" in msg, msg
            # "*" covers other accounts: 100 core-h
            other_acct = copy.deepcopy(good)
            other_acct["attributes"].update(account="hp000000", duration=f"{100 // cores + 1:02d}:00:00")
            refused(other_acct, "over the budget of 100 core-h", "account hp000000")
            # no budget configured: no budget check
            cfg.write_text(json.dumps({"ssh": {"host": "hokusai.example"},
                                       "defaults": {"account": "rb000000"}}))
            n = len(submitted)
            submit(over)
            assert len(submitted) == n + 1, "refused with no budget configured"

            # off again: a hand-written spec submits
            os.environ["HPC_SUBMIT_GUARD"] = "0"
            submit(hand)
