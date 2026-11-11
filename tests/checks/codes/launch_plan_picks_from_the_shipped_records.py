import os
import tempfile
from unittest.mock import patch
from checks._common import no_user_config

LABEL = 'launch_plan picks from the shipped records'


def launch_plan_picks_from_the_shipped_records():
    """launch_plan against HOKUSAI's shipped benchmarks: the match ladder,
    cost vs time, a fixed node count, the job spec, and that it answers
    an unconfigured facility (it never reaches the cluster)."""
    from hpc_codes import server
    from hpc_codes.cp2k import tools

    def must_not_run(*_):
        raise AssertionError("launch_plan reached run_command")
    env = {k: v for k, v in os.environ.items() if k != "HOKUSAI_HOST"}
    with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, env, clear=True), no_user_config("hokusai"), \
            patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}), \
            patch.object(tools, "run_command", must_not_run):
        build = {"build_id": "cp2k-2026.2-psmp-cpu-gcc", "facility": "hokusai", "code": "cp2k",
                 "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"},
                 "toolchain": "gcc", "install_path": "software/cp2k",
                 "env_script": "~/software/cp2k/cp2k_env.sh",
                 "test_status": {"level": "none", "result": "pass"},
                 "default": True, "date": "2026-11-04"}
        server.records_append("hokusai", "software_registry", build)
        uio = {"label": "UiO-66-conv", "atoms": 456}

        cost = server.launch_plan("hokusai", "cp2k", "aimd_step", uio, steps=1000)
        assert cost["basis"]["match"] == "exact", cost
        assert cost["layout"] == {"nodes": 1, "ranks_per_node": 16, "threads_per_rank": 7}, cost
        assert cost["estimate"]["value"] == 9.34 and cost["estimate"]["suggested_duration"] == "04:30:00", cost
        assert cost["warnings"] == [], cost
        spec = cost["job_spec"]
        assert spec["pre_launch"] == "source ~/software/cp2k/cp2k_env.sh", spec
        assert spec["launcher"].startswith("srun --mpi=pmix --cpus-per-task=7 "), spec
        assert spec["environment"]["OMP_NUM_THREADS"] == "7", spec
        assert spec["resources"] == {"node_count": 1, "processes_per_node": 16,
                                     "cpu_cores_per_process": 7}, spec
        assert spec["attributes"] == {"queue_name": "mpc"}, spec

        # time: 2 nodes (82 % efficient) beats 4 (57 %, below the 70 % floor)
        fast = server.launch_plan("hokusai", "cp2k", "aimd_step", uio, optimize="time")
        assert fast["layout"]["nodes"] == 2 and fast["estimate"]["value"] == 5.72, fast

        # an unmeasured node count borrows the nearest layout, with no estimate
        three = server.launch_plan("hokusai", "cp2k", "aimd_step", uio, nodes=3)
        assert three["layout"]["nodes"] == 3 and three["estimate"] is None, three
        assert any("no timing on 3 node(s)" in w for w in three["warnings"]), three

        near = server.launch_plan("hokusai", "cp2k", "aimd_step", {"atoms": 420})
        assert near["basis"]["match"] == "similar" and near["basis"]["system"] == "UiO-66-conv", near
        assert near["estimate"]["rough"], near

        none = server.launch_plan("hokusai", "cp2k", "geo_opt", {"label": "X", "atoms": 50})
        assert none["basis"]["match"] == "none" and none["estimate"] is None, none
        assert none["layout"]["ranks_per_node"] * none["layout"]["threads_per_rank"] == 112, none

        # a removed build is refused; a facility without the pack is refused
        server.records_append("hokusai", "software_registry",
                              {**build, "build_id": "cp2k-gone", "default": False,
                               "removed": "2026-11-04"})
        for call, text in ((lambda: server.launch_plan("hokusai", "cp2k", "aimd_step", uio,
                                                       build_id="cp2k-gone"), "No build"),
                           (lambda: server.launch_plan("rikyu", "cp2k", "aimd_step", uio),
                            "has not opted into"),
                           (lambda: server.launch_plan("hokusai", "cp2k", "md", uio), "run_type")):
            try:
                call()
            except ValueError as e:
                assert text in str(e), e
            else:
                raise AssertionError(f"launch_plan did not refuse ({text})")
