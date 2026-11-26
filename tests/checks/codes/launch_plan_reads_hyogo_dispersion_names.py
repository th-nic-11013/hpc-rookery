import os
import tempfile
from unittest.mock import patch
from checks._common import no_user_config

LABEL = 'launch_plan reads hyogo dispersion names'


def launch_plan_reads_hyogo_dispersion_names():
    """kairo live run 1, gap 20: records say dispersion "D3" while hyogo's
    cp2k_make_input says d3="zero"; method={"dispersion": "zero"} matched
    nothing and said nothing. "zero", "D3(0)" and "D3" are now the same
    (as are "bj", "D3(BJ)", "D3BJ" and none/null), and a dispersion that
    is not recorded gets basis.why naming the recorded values and both
    spellings."""
    from hpc_codes import launch, server
    from hpc_codes.cp2k import tools

    for a, b in [("zero", "D3"), ("d3(0)", "D3"), ("bj", "D3BJ"), ("D3(BJ)", "d3bj"),
                 ("none", None), ("null", None)]:
        assert launch._dispersion(a) == launch._dispersion(b), (a, b)
    assert launch._dispersion("zero") != launch._dispersion("bj")
    assert launch._dispersion("D3") != launch._dispersion(None)

    def must_not_run(*_):
        raise AssertionError("launch_plan reached run_command")
    env = {k: v for k, v in os.environ.items() if k != "HOKUSAI_HOST"}
    with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, env, clear=True), no_user_config("hokusai"), \
            patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}), \
            patch.object(tools, "run_command", must_not_run):
        server.records_append("hokusai", "software_registry", {
            "build_id": "cp2k-2026.2-psmp-cpu-gcc", "facility": "hokusai", "code": "cp2k",
            "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"},
            "toolchain": "gcc", "install_path": "software/cp2k",
            "env_script": "~/software/cp2k/cp2k_env.sh",
            "test_status": {"level": "none", "result": "pass"},
            "default": True, "date": "2026-11-04"})
        uio = {"label": "UiO-66-prim", "atoms": 114}
        ids = None
        for d in ("D3", "zero", "d3(0)"):
            p = server.launch_plan("hokusai", "cp2k", "aimd_step", uio,
                                   method={"functional": "revPBE", "dispersion": d})
            assert p["basis"]["match"] == "exact", (d, p["basis"])
            ids = ids or p["basis"]["record_ids"]
            assert p["basis"]["record_ids"] == ids, (d, p["basis"]["record_ids"], ids)

        for d in ("bj", "none"):
            p = server.launch_plan("hokusai", "cp2k", "aimd_step", uio,
                                   method={"functional": "revPBE", "dispersion": d})
            why = p["basis"]["why"]
            assert p["basis"]["match"] == "none", (d, p["basis"])
            assert "dispersion: D3" in why and 'd3="zero"' in why, why

        p = server.launch_plan("hokusai", "cp2k", "aimd_step", {"label": "nowhere", "atoms": 5000},
                               method={"dispersion": "bj"})
        why = p["basis"]["why"]
        assert p["basis"]["match"] == "none" and "recorded method values" in why, why
        assert "D3" in why and 'd3="bj"' in why, why
