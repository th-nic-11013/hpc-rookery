import os
import tempfile
from unittest.mock import patch
from checks._common import no_user_config

LABEL = 'launch_plan checks hybrid memory (G7)'


def launch_plan_checks_hybrid_memory():
    """G7: with a hybrid record that carries its HFX memory, launch_plan
    reports the integrals per rank and the estimated peak against the
    rank's share of node memory (HOKUSAI mpc: 112 GiB), skips a node
    count that wouldn't fit, warns on a fixed one and on an HFX
    MAX_MEMORY below the integrals per rank, and says when a hybrid
    record has no memory to check."""
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
        base = next(r for r in server.records_query("hokusai", "benchmarks",
                                                    {"run_type": "single_point"})
                    if r["system"]["label"] == "UiO-66-conv")
        base = {k: v for k, v in base.items() if k != "_scope"}
        m = {**base["method"], "functional": "PBE0", "hybrid": True}

        def rec(i, nodes, wall, extra=None, functional="PBE0"):
            server.records_append("hokusai", "benchmarks", dict(
                base, id=f"g7-probe-{i}", job_id=str(i), date="2026-11-05", nodes=nodes,
                ranks_per_node=16, threads_per_rank=7, method={**m, "functional": functional},
                metric={"name": "wall_s", "value": wall}, **({"extra": extra} if extra else {})))
        uio = {"label": "UiO-66-conv", "atoms": 456}
        # no memory recorded: the check says it was skipped
        rec(1, 2, 582.0, functional="HSE06")
        p = server.launch_plan("hokusai", "cp2k", "single_point", uio, method={"functional": "HSE06"})
        assert p["memory"] is None, p
        assert any("no recorded HFX memory" in w for w in p["warnings"]), p["warnings"]
        # 64000 MiB of ERIs: 1 node (4000 MiB/rank + 2573 base > 0.9 x 7168)
        # is cheaper but can't hold them; 2 nodes can
        rec(2, 1, 1000.0)
        rec(3, 2, 582.0, {"hfx_eri_mib": 64000, "peak_mib_per_rank": 4573})
        p = server.launch_plan("hokusai", "cp2k", "single_point", uio, method={"functional": "PBE0"})
        assert p["layout"]["nodes"] == 2, p
        mem = p["memory"]
        assert mem["eri_per_rank_mib"] == 2000 and mem["est_peak_mib_per_rank"] == 4573, mem
        assert mem["limit_mib_per_rank"] == 6451 and mem["fits"] is True, mem
        assert mem["suggested_hfx_memory_mb"] == 2400 and mem["record_id"] == "g7-probe-3", mem
        one = next(o for o in p["basis"]["options"] if o["nodes"] == 1)
        assert one["memory"]["fits"] is False and one["memory"]["est_peak_mib_per_rank"] == 6573, one
        assert any("[1] node(s) would not hold" in w for w in p["warnings"]), p["warnings"]
        # a fixed node count that doesn't fit, and a MAX_MEMORY below the ERIs per rank
        p = server.launch_plan("hokusai", "cp2k", "single_point", uio,
                               method={"functional": "PBE0"}, nodes=1, hfx_memory_mb=3000)
        assert p["memory"]["fits"] is False, p["memory"]
        assert any("out-of-memory" in w for w in p["warnings"]), p["warnings"]
        assert any("on the fly" in w for w in p["warnings"]), p["warnings"]
        p = server.launch_plan("hokusai", "cp2k", "single_point", uio,
                               method={"functional": "PBE0"}, hfx_memory_mb=3000)
        assert not any("on the fly" in w or "out-of-memory" in w for w in p["warnings"]), p
        try:
            server.launch_plan("hokusai", "cp2k", "single_point", uio, hfx_memory_mb=0)
        except ValueError as e:
            assert "MAX_MEMORY" in str(e), e
        else:
            raise AssertionError("hfx_memory_mb=0 accepted")
        # G33: a correction (supersedes) replaces the record it names
        # (newer than g7-probe-3, so without the fix the superseded one is chosen)
        for rid, more in (("g7-probe-4", {}), ("g7-probe-4-v2", {"supersedes": "g7-probe-4"})):
            server.records_append("hokusai", "benchmarks", dict(
                base, id=rid, job_id="4", date="2026-11-06", nodes=2, ranks_per_node=16,
                threads_per_rank=7, method=m, metric={"name": "wall_s", "value": 600.0},
                extra={"hfx_eri_mib": 32000, "peak_mib_per_rank": 4000}, **more))
        p = server.launch_plan("hokusai", "cp2k", "single_point", uio, method={"functional": "PBE0"})
        assert p["memory"]["record_id"] == "g7-probe-4-v2", p["memory"]
        assert "g7-probe-4" not in p["basis"]["record_ids"], p["basis"]
        # a GGA plan carries no memory block and no memory warning
        g = server.launch_plan("hokusai", "cp2k", "aimd_step", uio)
        assert g["memory"] is None and not any("memory" in w for w in g["warnings"]), g
