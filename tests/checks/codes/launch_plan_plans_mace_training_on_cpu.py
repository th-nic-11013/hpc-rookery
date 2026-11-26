import os
import tempfile
from unittest.mock import patch
from checks._common import no_user_config

LABEL = 'launch_plan plans MACE training on CPU nodes (device="cpu")'


def launch_plan_plans_mace_training_on_cpu():
    """MACE training on HOKUSAI mpc ran 4-10x slower with threads left
    unbound or spread over both sockets (cpu-scan, 2026-11-25). The mace
    pack's MACE_CPU value holds the measured layout, and launch_plan(
    device="cpu") turns it into a job spec: one process, threads bound to
    cores, the node to itself. GPU plans and the CPU packs are unchanged.
    Records are made in a temporary user folder."""
    from hpc_codes import server
    from hpc_codes.cp2k import tools

    def must_not_run(*_):
        raise AssertionError("launch_plan reached run_command")
    env = {k: v for k, v in os.environ.items() if k not in ("HOKUSAI_HOST", "HPC_RECORDS_ROOT")}
    with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, env, clear=True), no_user_config("hokusai"), \
            patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}), \
            patch.object(tools, "run_command", must_not_run):
        server.records_append("hokusai", "software_registry", {
            "facility": "hokusai", "toolchain": "gcc", "install_path": "software/mlip/2026.10",
            "env_script": "~/software/mlip/2026.10/mlip_env.sh",
            "test_status": {"level": "none", "result": "pass"}, "default": True, "date": "2026-10-15",
            "build_id": "mace-0.3.14-venv-gpu-uv", "code": "mace", "version": "0.3.14",
            "variant": {"exe": "other", "target": "gpu"}})
        uio = {"atoms": 456, "label": "UiO-66-conv"}

        cpu = server.launch_plan("hokusai", "mace", "train", uio, device="cpu")
        assert cpu["layout"] == {"nodes": 1, "ranks_per_node": 1, "threads_per_rank": 56, "gpus": 0,
                                 "distributed": False, "device": "cpu"}, cpu["layout"]
        js = cpu["job_spec"]
        assert js["pre_launch"] == "source ~/software/mlip/2026.10/mlip_env.sh", js
        assert js["launcher"] == "srun --ntasks=1 --cpus-per-task=56 --cpu-bind=cores", js
        assert js["environment"] == {"OMP_NUM_THREADS": "56", "MKL_NUM_THREADS": "56",
                                     "OMP_PROC_BIND": "close", "OMP_PLACES": "cores"}, js
        # batch 16 at 28 threads peaked at 35.8 GB (job 9976295): the node's memory, not 1 GB/core
        assert js["resources"] == {"node_count": 1, "processes_per_node": 1, "cpu_cores_per_process": 56,
                                   "exclusive_node_use": True, "memory": 100 * 2**30}, js["resources"]
        assert js["attributes"] == {"queue_name": "mpc"}, js
        assert cpu["estimate"] is None and "9970023" in cpu["basis"]["why"], cpu["basis"]
        assert any("billed for all 112 cores" in w for w in cpu["warnings"]), cpu["warnings"]

        # the GPU plan is what it was, with or without device="gpu"
        for gpu in (server.launch_plan("hokusai", "mace", "train", uio),
                    server.launch_plan("hokusai", "mace", "train", uio, device="gpu")):
            assert gpu["layout"]["gpus"] == 1 and gpu["job_spec"]["attributes"] == {"queue_name": "gpu"}, gpu

        for call, text in (
                (lambda: server.launch_plan("hokusai", "mace", "train", uio, device="cpu", gpus=1), "takes no gpus"),
                (lambda: server.launch_plan("hokusai", "mace", "train", uio, device="cpu", nodes=2), "one node per job"),
                (lambda: server.launch_plan("hokusai", "mace", "train", uio, device="tpu"), 'device must be'),
                (lambda: server.launch_plan("hokusai", "cp2k", "aimd_step", uio, device="gpu"), "CPU pack")):
            try:
                call()
            except ValueError as e:
                assert text in str(e), f"{text!r} not in: {e}"
            else:
                raise AssertionError(f"launch_plan did not refuse ({text})")

        # a facility whose mace pack has no MACE_CPU gets a refusal that says how to add one
        from hpc_codes import packs
        vals = {k: v for k, v in packs.pack_values("hokusai", "mace").items() if k != "MACE_CPU"}
        with patch.object(packs, "pack_values", lambda *_: vals):
            try:
                server.launch_plan("hokusai", "mace", "train", uio, device="cpu")
            except ValueError as e:
                assert "no MACE_CPU layout" in str(e) and "values.json" in str(e), e
            else:
                raise AssertionError("launch_plan planned CPU training without MACE_CPU")
