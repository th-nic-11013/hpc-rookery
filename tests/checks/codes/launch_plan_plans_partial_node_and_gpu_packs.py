import os
import tempfile
from unittest.mock import patch
from checks._common import no_user_config

LABEL = 'launch_plan plans partial-node and GPU packs (G38)'


def launch_plan_plans_partial_node_and_gpu_packs():
    """G38: an MLIP MD run is fastest on part of a node (1 rank x 56
    threads of 112) and training runs on GPUs with no timing at all;
    launch_plan refused both shapes. The lammps pack (partial node) and
    the mace pack (GPUs) declare their shape in their values; CP2K's
    fill-the-node rule is untouched. Records are made here, in a
    temporary user folder: nothing is read from the user's own."""
    from hpc_codes import server
    from hpc_codes.cp2k import tools

    def must_not_run(*_):
        raise AssertionError("launch_plan reached run_command")
    env = {k: v for k, v in os.environ.items() if k not in ("HOKUSAI_HOST", "HPC_RECORDS_ROOT")}
    with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, env, clear=True), no_user_config("hokusai"), \
            patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}), \
            patch.object(tools, "run_command", must_not_run):
        uio = {"label": "UiO-66-conv", "atoms": 456}
        base = {"facility": "hokusai", "toolchain": "gcc", "install_path": "software/mlip/2026.10",
                "env_script": "~/software/mlip/2026.10/mlip_env.sh",
                "test_status": {"level": "none", "result": "pass"}, "default": True,
                "date": "2026-10-15"}
        lmp_build = {**base, "build_id": "lammps-4jul2026-metatomic-mpi-cpu-gcc", "code": "lammps",
                     "version": "4 Jul 2026", "variant": {"exe": "other", "target": "cpu"}}
        # nothing recorded: the pack's default layout, no estimate, no refusal
        server.records_append("hokusai", "software_registry", lmp_build)
        bare = server.launch_plan("hokusai", "lammps", "md_step", uio)
        assert bare["layout"] == {"nodes": 1, "ranks_per_node": 1, "threads_per_rank": 56}, bare["layout"]
        assert bare["estimate"] is None and "LAMMPS_DEFAULT_LAYOUT" in bare["basis"]["why"], bare["basis"]

        def bench(R, T, value, precision="f64"):
            return {"facility": "hokusai", "partition": "mpc", "node_type": "MPC (bwmpc), 112 cores",
                    "code": "lammps", "version": "4 Jul 2026", "build_id": lmp_build["build_id"],
                    "system": {"label": "UiO-66-conv", "atoms": 456,
                               "elements": {"Zr": 24, "O": 128, "C": 192, "H": 112},
                               "periodic": "xyz", "kpoints": "none"},
                    "method": {"model": "MACE-MP-0 medium", "precision": precision,
                               "interface": "LAMMPS pair_style metatomic", "device": "cpu"},
                    "run_type": "md_step", "nodes": 1, "ranks_per_node": R, "threads_per_rank": T,
                    "job_id": None, "date": "2026-10-20", "valid": True,
                    "id": f"hokusai-lammps-2026-10-20-uio66conv-mdstep-macemp0m-{precision}-1n-{R}x{T}",
                    "metric": {"name": "s_per_step", "value": value}}
        # the measured scan (bench/results.csv, 2026-10-20)
        for R, T, v in ((1, 112, 0.8408), (1, 56, 0.449), (1, 28, 0.6065), (1, 14, 0.98), (2, 56, 0.4766)):
            server.records_append("hokusai", "benchmarks", bench(R, T, v))
        server.records_append("hokusai", "benchmarks", bench(1, 56, 0.2688, "f32"))

        fast = server.launch_plan("hokusai", "lammps", "md_step", uio, method={"precision": "f64"},
                                  optimize="time", steps=20000)
        assert fast["basis"]["match"] == "exact", fast["basis"]
        assert fast["layout"] == {"nodes": 1, "ranks_per_node": 1, "threads_per_rank": 56}, fast["layout"]
        est = fast["estimate"]
        # cost is counted on the 56 cores used, not the node's 112
        assert est["value"] == 0.449 and est["core_hours_per_unit"] == round(0.449 * 56 / 3600, 4), est
        assert est["total_core_hours"] == round(0.449 * 20000 * 56 / 3600, 2), est
        assert any("uses 56 of 112 cores" in w for w in fast["warnings"]), fast["warnings"]
        js = fast["job_spec"]
        assert js["launcher"] == "srun --mpi=pmix --ntasks=1 --cpus-per-task=56 --cpu-bind=cores", js
        assert js["environment"]["OMP_NUM_THREADS"] == "56" and js["environment"]["PMIX_MCA_psec"] == "native"
        assert js["resources"] == {"node_count": 1, "processes_per_node": 1, "cpu_cores_per_process": 56}
        # optimize="cost" picks the fewest core-seconds per step: 14 threads (13.7) beat 56 (25.1)
        cheap = server.launch_plan("hokusai", "lammps", "md_step", uio, method={"precision": "f64"})
        assert cheap["layout"]["threads_per_rank"] == 14 and "cheapest layout" in cheap["basis"]["why"], cheap
        f32 = server.launch_plan("hokusai", "lammps", "md_step", uio, method={"precision": "f32"})
        assert f32["estimate"]["value"] == 0.2688 and f32["layout"]["threads_per_rank"] == 56, f32

        # a GPU pack: one process per GPU, the cores a GPU is billed as, no timing needed
        server.records_append("hokusai", "software_registry", {
            **base, "build_id": "mace-0.3.14-venv-gpu-uv", "code": "mace", "version": "0.3.14",
            "variant": {"exe": "other", "target": "gpu"}})
        one = server.launch_plan("hokusai", "mace", "train", {"atoms": 456})
        assert one["build"]["version"] == "0.3.14" and one["estimate"] is None
        assert one["layout"] == {"nodes": 1, "ranks_per_node": 1, "threads_per_rank": 28, "gpus": 1,
                                 "distributed": False}, one["layout"]
        two = server.launch_plan("hokusai", "mace", "train", {"atoms": 456}, gpus=2)
        assert two["layout"]["distributed"] is True
        assert two["job_spec"]["resources"] == {"node_count": 1, "processes_per_node": 2,
                                                "cpu_cores_per_process": 28, "gpus": 2}, two["job_spec"]
        assert two["job_spec"]["launcher"] == "srun --ntasks=2 --cpus-per-task=28 --gpus-per-task=1"
        assert two["job_spec"]["attributes"] == {"queue_name": "gpu"}
        assert any("billed as 56 cores" in w for w in two["warnings"]), two["warnings"]
        for call, text in ((lambda: server.launch_plan("hokusai", "mace", "train", uio, gpus=5), "gpus must be 1 to 4"),
                           (lambda: server.launch_plan("hokusai", "mace", "train", uio, nodes=2), "one node per job"),
                           (lambda: server.launch_plan("hokusai", "lammps", "md_step", uio, gpus=1), "CPU pack"),
                           (lambda: server.launch_plan("hokusai", "cp2k", "aimd_step", uio, gpus=1), "CPU pack")):
            try:
                call()
            except ValueError as e:
                assert text in str(e), f"{text!r} not in: {e}"
            else:
                raise AssertionError(f"launch_plan did not refuse ({text})")
        # the model table names files and where each came from
        from hpc_codes import packs
        models = packs.pack_values("hokusai", "mace")["MACE_MODELS"]
        for name, m in models.items():
            assert len(m["sha256"]) == 64 and m["model"].endswith(".model") and m["theory"], name
            assert "path" in m["replay"] and m["replay"]["source"], name
        exes = packs.pack_values("hokusai", "lammps")["LAMMPS_EXE"]
        assert lmp_build["build_id"] in exes and all(v.endswith("/lmp_mpi") for v in exes.values())
