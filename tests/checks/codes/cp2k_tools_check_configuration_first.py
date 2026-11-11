import os
from unittest.mock import patch
from checks._common import no_user_config

LABEL = 'cp2k tools check configuration first'


def cp2k_tools_check_configuration_first():
    from hpc_codes import server
    from hpc_codes.cp2k import tools

    def must_not_run(*_):
        raise AssertionError("an unconfigured facility reached run_command")
    env = {k: v for k, v in os.environ.items() if k != "HOKUSAI_HOST"}
    # clear the environment first: no_user_config's override must survive it
    with patch.dict(os.environ, env, clear=True), no_user_config("hokusai"), \
            patch.object(tools, "run_command", must_not_run):
        for call in (lambda: server.cp2k_bench_parse("hokusai", ["x"]),
                     lambda: server.cp2k_check_input("hokusai", "x.inp"),
                     lambda: server.cp2k_check_build("hokusai"),
                     lambda: server.cp2k_kinds("hokusai", ["H"]),
                     lambda: server.cp2k_bench_input("hokusai", "x.xyz", "x", "out"),
                     lambda: server.cp2k_bench_dryrun("hokusai", "x", "y"),
                     lambda: server.cp2k_bench_records("hokusai", {}, {}, "aimd_step",
                                                       stage_dirs=["x"])):
            try:
                call()
            except RuntimeError as e:
                assert "not configured yet" in str(e), e
            else:
                raise AssertionError("unconfigured cp2k tool did not stop")
