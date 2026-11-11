import os
import tempfile
from unittest.mock import patch
from checks._common import ROOT

LABEL = 'cp2k_record_from_run records a parsed production run (G24)'


def cp2k_record_from_run_records_a_parsed_run():
    """G24: a cp2k_parse_output result plus `like` (a record of the same
    system and method) becomes a benchmarks record with the id, timing
    and extra fields the hand-written 2026-11-05 records had; refusals
    append nothing."""
    import gzip
    from hyogo.cp2k import output
    from hpc_codes import records, server
    root = ROOT
    fx = root / "tests" / "fixtures" / "cp2k_outputs"
    parse = lambda n: output.parse(gzip.decompress((fx / f"{n}.out.gz").read_bytes()).decode())
    sp, opt = parse("r2scan-sp"), parse("hse06-cellopt")
    with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}):
        env = {k: v for k, v in os.environ.items() if k != "HPC_RECORDS_ROOT"}
        with patch.dict(os.environ, env, clear=True):
            server.records_append("hokusai", "software_registry", {
                "build_id": "cp2k-2026.2-fake", "facility": "hokusai", "code": "cp2k",
                "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"},
                "toolchain": "fake", "install_path": tmp, "env_script": tmp + "/env.sh",
                "test_status": {"level": "none", "result": "pass"}, "default": True,
                "date": "2026-11-04"})
            like = {"id": "hokusai-cp2k-2026-11-04-uio66conv-singlepoint-pbe-1n-16x7",
                    "facility": "hokusai", "partition": "mpc", "node_type": "MPC test",
                    "code": "cp2k", "version": "2026.2", "build_id": "cp2k-2026.2-fake",
                    "system": {"label": "UiO-66-conv", "atoms": 456,
                               "elements": {"Zr": 24, "O": 128, "C": 192, "H": 112},
                               "cell_volume_A3": 9115, "periodic": "xyz", "kpoints": "gamma"},
                    "method": {"functional": "PBE", "dispersion": "D3(BJ)", "basis": "DZVP",
                               "cutoff_ry": 1000, "hybrid": False, "scf": "OT DIIS"},
                    "run_type": "single_point", "nodes": 1, "ranks_per_node": 16,
                    "threads_per_rank": 7, "metric": {"name": "wall_s", "value": 100.0},
                    "date": "2026-11-04", "valid": True}
            server.records_append("hokusai", "benchmarks", like)
            count = lambda: len(records.query("hokusai", "benchmarks", {}))

            r = server.cp2k_record_from_run("hokusai", sp, 1, like=like["id"],
                                            system={"cell_volume_A3": 9115},
                                            method={"functional": "r2SCAN"}, job_id="9737100")
            rec = r["record"]
            assert r["appended"] and r["valid"] and not r["warnings"], r
            assert r["id"] == "hokusai-cp2k-2026-11-05-uio66conv-singlepoint-r2scan-1n-16x7", r["id"]
            assert rec["metric"] == {"name": "wall_s", "value": 279.886}, rec["metric"]
            assert (rec["ranks_per_node"], rec["threads_per_rank"]) == (16, 7)
            assert rec["method"]["functional"] == "r2SCAN" and rec["method"]["cutoff_ry"] == 1000
            assert rec["system"]["electrons"] == 1936 and rec["system"]["cell_volume_A3"] == 9115
            assert rec["extra"] == {"forces_s": 248.6, "scf_steps": 55,
                                    "peak_mib_per_rank": 3276, "core_hours": 8.7}, rec["extra"]
            assert rec["job_id"] is None and r["provenance"]["job_ids"] == ["9737100"]
            assert records.query("hokusai", "benchmarks_provenance", {"id": r["id"]})

            r = server.cp2k_record_from_run("hokusai", opt, 2, like=like["id"],
                                            method={"functional": "HSE06", "hybrid": True},
                                            dry_run=True)
            rec = r["record"]
            assert not r["appended"] and r["valid"], r
            assert r["id"] == "hokusai-cp2k-2026-11-05-uio66conv-cellopt-hse06-2n-16x7", r["id"]
            assert rec["metric"] == {"name": "s_per_step", "value": 423.159}, rec["metric"]
            assert rec["extra"]["opt_steps"] == 21 and rec["extra"]["final_pressure_bar"] == 7.34
            assert rec["extra"]["hfx_eri_mib"] == 18952 and rec["extra"]["core_hours"] == 552.9
            assert rec["extra"]["first_step_s"] == 439.4 and len(opt["opt"]["step_times_s"]) == 22
            assert "converged in 21 steps" in rec["notes"] and r["provenance"] is None
            # G32: an optimisation's volume is its own final cell, not like's
            assert rec["system"]["cell_volume_A3"] == round(opt["cell"]["volume"], 1) != 9115, rec
            # G32: a single point prints no cell, so like's volume (maybe another
            # geometry) is dropped with a warning instead of copied
            r = server.cp2k_record_from_run("hokusai", sp, 1, like=like["id"], tag="g32",
                                            dry_run=True)
            assert "cell_volume_A3" not in r["record"]["system"], r["record"]["system"]
            assert any("cell_volume_A3 not copied" in w for w in r["warnings"]), r["warnings"]

            # an optimisation cut by the walltime: still a valid per-step time, warned
            text = gzip.decompress((fx / "hse06-cellopt.out.gz").read_bytes()).decode()
            cut = output.parse(text[: text.index(" OPTIMIZATION STEP:     12\n")])
            r = server.cp2k_record_from_run("hokusai", cut, 2, like=like["id"], tag="cut",
                                            dry_run=True)
            rec = r["record"]
            assert r["valid"] and rec["extra"]["opt_steps"] == 11, rec
            times = cut["opt"]["step_times_s"]
            assert len(times) == 12 and rec["extra"]["first_step_s"] == 439.4, times
            assert abs(rec["metric"]["value"] - sum(times) / 11) < 1e-3, rec["metric"]
            assert any("did not converge" in w for w in r["warnings"]), r["warnings"]
            # unconverged SCF: recorded, but not as a usable timing
            bad = {**sp, "scf": {**sp["scf"], "not_converged": 1}}
            r = server.cp2k_record_from_run("hokusai", bad, 1, like=like["id"], tag="bad",
                                            dry_run=True)
            assert not r["valid"] and "SCF not converged" in r["record"]["reason"], r

            before = count()
            for args, kw, msg in (
                    ((sp, 1), {"like": like["id"]}, "already exists"),   # the r2SCAN record above
                    ((sp, 3), {"like": like["id"]}, "do not divide"),
                    ((sp, 1), {"like": "no-such-record"}, "no benchmarks record"),
                    ((sp, 1), {}, "pass like="),
                    (({**sp, "natoms": 455}, 1), {"like": like["id"]}, "atoms"),
                    (({k: v for k, v in sp.items() if k != "ranks"}, 1), {"like": like["id"]},
                     "older cp2k_parse_output"),
                    (({"energy": 1}, 1), {"like": like["id"]}, "cp2k_parse_output result")):
                try:
                    server.cp2k_record_from_run("hokusai", *args,
                                                **({"method": {"functional": "r2SCAN"}}
                                                   if msg == "already exists" else {}), **kw)
                except ValueError as e:
                    assert msg in str(e), (msg, str(e))
                else:
                    raise AssertionError(f"expected a refusal naming {msg!r}")
            assert count() == before, "a refused call appended a record"
            assert not server.records_check("hokusai")["errors"]
