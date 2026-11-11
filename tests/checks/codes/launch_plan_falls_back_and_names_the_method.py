import os
import tempfile
from unittest.mock import patch
from checks._common import no_user_config

LABEL = 'launch_plan falls back to single points and names the method'


def launch_plan_falls_back_and_names_the_method():
    """G17: an optimisation with no geo_opt/cell_opt record borrows the
    same system's single_point time per step (rough, with a warning), and
    a real cell_opt record wins once there is one. G20: a recorded label
    that the method filter empties says so and lists the recorded
    methods, with no estimate."""
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
        sp = server.launch_plan("hokusai", "cp2k", "single_point", uio)
        assert sp["basis"]["match"] == "exact" and sp["basis"]["run_type"] == "single_point", sp
        assert not sp["estimate"]["rough"] and sp["estimate"]["metric"] == "wall_s", sp

        # (1) only a single point: its time stands in for one step
        opt = server.launch_plan("hokusai", "cp2k", "cell_opt", uio, steps=20)
        assert opt["basis"]["match"] == "exact" and opt["basis"]["run_type"] == "single_point", opt
        assert opt["basis"]["record_ids"] == sp["basis"]["record_ids"], opt
        assert opt["layout"] == sp["layout"], opt
        est = opt["estimate"]
        assert est["metric"] == "s_per_step" and est["rough"], est
        assert est["value"] == sp["estimate"]["value"], est
        assert est["total_wall_s"] == round(est["value"] * 20, 1), est
        assert any("no cell_opt timing for UiO-66-conv" in w and "single_point" in w
                   for w in opt["warnings"]), opt
        geo = server.launch_plan("hokusai", "cp2k", "geo_opt", uio)
        assert geo["basis"]["run_type"] == "single_point" and geo["estimate"]["total_wall_s"] is None, geo
        # an unrecorded label still gets no estimate
        none = server.launch_plan("hokusai", "cp2k", "cell_opt", {"label": "X", "atoms": 50})
        assert none["basis"]["match"] == "none" and none["estimate"] is None, none
        assert "no recorded system like this" in none["basis"]["why"], none

        # (2) a cell_opt record of the same system is preferred
        base = next(r for r in server.records_query("hokusai", "benchmarks",
                                                    {"run_type": "single_point"})
                    if r["id"] == sp["basis"]["record_ids"][0])
        cell = dict({k: v for k, v in base.items() if k != "_scope"},
                    id="launch-plan-cellopt-probe", run_type="cell_opt", job_id="1",
                    metric={"name": "s_per_step", "value": 41.0}, date="2026-11-05")
        server.records_append("hokusai", "benchmarks", cell)
        opt = server.launch_plan("hokusai", "cp2k", "cell_opt", uio, steps=20)
        assert opt["basis"]["run_type"] == "cell_opt", opt
        assert opt["basis"]["record_ids"] == ["launch-plan-cellopt-probe"], opt
        assert opt["estimate"] == {**opt["estimate"], "metric": "s_per_step", "value": 41.0,
                                   "rough": False, "total_wall_s": 820.0}, opt
        assert not any("single_point" in w for w in opt["warnings"]), opt

        # (3) the label is recorded, but not with this functional
        for rt in ("single_point", "aimd_step", "geo_opt"):
            other = server.launch_plan("hokusai", "cp2k", rt, uio, method={"functional": "r2SCAN"})
            assert other["basis"]["match"] == "none" and other["estimate"] is None, other
            why = other["basis"]["why"]
            assert "UiO-66-conv is recorded, but not with functional=r2SCAN" in why, why
            assert "functional: revPBE" in why and "no recorded system" not in why, why
