import os
import pathlib
import sys
import tempfile
from unittest.mock import patch
from checks._common import ROOT, local_run_command

LABEL = 'cp2k_bench_records appends benchmarks and provenance'


def cp2k_bench_records_appends_benchmarks_and_provenance():
    """cp2k_bench_records on the scrubbed smoke-stage runs, laid out as a
    job's stage folder with a repeat and a killed run: one record per
    layout, the killed one valid: false, job IDs only in provenance, and
    nothing written when any record is refused."""
    import shutil
    import yaml
    from hpc_codes import records, server
    from hpc_codes.cp2k import tools
    real_pack_values = tools.pack_values
    root = ROOT
    fixture = root / "tests" / "fixtures" / "cp2k_bench" / "stage1"
    values = {**tools.pack_values("hokusai"), "CP2K_PYTHON": sys.executable}
    system = {"label": "H2O-32", "atoms": 96, "elements": {"H": 64, "O": 32},
              "periodic": "xyz", "kpoints": "gamma"}
    method = {"functional": "PADE (LDA)", "basis": "DZVP-GTH", "cutoff_ry": 280,
              "hybrid": False, "scf": "OT DIIS"}
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(tools, "run_command", local_run_command), \
            patch.object(tools, "pack_values", lambda f, code="cp2k": (
                values if f == "hokusai" else real_pack_values(f, code))), \
            patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp, "HOKUSAI_HOST": "localhost"}):
        tmp = pathlib.Path(tmp)
        stage = tmp / "runs" / "9000001" / "stage2"
        shutil.copytree(fixture, stage)
        shutil.copytree(stage / "H2O-32_8x14_r1", stage / "H2O-32_8x14_r2")
        killed = stage / "H2O-32_112x1_r1"
        shutil.copytree(stage / "H2O-32_28x4_r1", killed)
        out = killed / "cp2k.out"
        out.write_text(out.read_text().replace("PROGRAM ENDED AT", "PROGRAM KILLED AT"))
        server.records_append("hokusai", "software_registry", {
            "build_id": "cp2k-2026.2-fake", "facility": "hokusai", "code": "cp2k",
            "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"},
            "toolchain": "fake", "install_path": str(tmp), "env_script": str(tmp / "env.sh"),
            "test_status": {"level": "none", "result": "pass"}, "default": True,
            "date": "2026-11-04"})

        parsed = server.cp2k_bench_parse("hokusai", [str(stage)])
        r = server.cp2k_bench_records("hokusai", system, method, "aimd_step", parsed=parsed,
                                      date="2026-11-04")
        base = "hokusai-cp2k-2026-11-04-h2o32-aimdstep-1n-"
        assert r["appended"] == [base + l for l in ("8x14", "28x4", "112x1")], r["appended"]
        assert r["valid"] == [base + "8x14", base + "28x4"], r
        assert "did not finish" in r["invalid"][base + "112x1"], r
        assert not r["check_errors"], r["check_errors"]
        by_id = {x["id"]: x for x in r["records"]}
        rec = by_id[base + "8x14"]
        assert rec["metric"]["name"] == "s_per_step" and rec["metric"]["value"] > 0, rec
        assert rec["ranks_per_node"] == 8 and rec["threads_per_rank"] == 14, rec
        assert rec["job_id"] is None and "2 repeats (2 usable)" in rec["notes"], rec
        # the shared HOKUSAI records' label, so launch_plan's queries line up
        assert rec["node_type"] == "MPC (bwmpc), 112 cores", rec
        assert rec["build_id"] == "cp2k-2026.2-fake" and rec["version"] == "2026.2", rec
        prov = server.records_query("hokusai", "benchmarks_provenance")
        assert {p["benchmark_id"] for p in prov} == set(r["appended"]), prov
        assert all(p["job_ids"] == ["9000001"] for p in prov), prov
        assert "9000001" not in (tmp / "benchmarks.yaml").read_text()
        assert not server.records_check("hokusai")["errors"]

        # an id that exists refuses the whole call; a tag makes new ids
        count = len(yaml.safe_load((tmp / "benchmarks.yaml").read_text())["records"])
        try:
            server.cp2k_bench_records("hokusai", system, method, "aimd_step", parsed=parsed,
                                      date="2026-11-04")
        except records.RecordsError as e:
            assert "already exists" in str(e) and "pass a tag" in str(e), e
        else:
            raise AssertionError("appended a benchmark id twice")
        assert len(yaml.safe_load((tmp / "benchmarks.yaml").read_text())["records"]) == count
        # parsing on the login node, and job IDs given explicitly
        r = server.cp2k_bench_records("hokusai", system, method, "aimd_step",
                                      stage_dirs=[str(stage)], date="2026-11-04", tag="lda",
                                      job_ids=["9000002"])
        assert all(i.endswith("-lda") for i in r["appended"]), r["appended"]
        assert server.records_query("hokusai", "benchmarks_provenance",
                                    {"id": r["appended"][0]})[0]["job_ids"] == ["9000002"]

        # refused before anything is written
        dry = tmp / "runs" / "dry-pass" / "stage2"
        shutil.copytree(fixture, dry)
        other = {**parsed, "runs": parsed["runs"] + [{**parsed["runs"][0], "input": "H2O-64"}]}
        for call, why in (
                (lambda: server.cp2k_bench_records("hokusai", system, method, "aimd_step",
                                                   stage_dirs=[str(dry)]), "dry-run"),
                (lambda: server.cp2k_bench_records("hokusai", system, method, "aimd_step",
                                                   parsed=other), "give input_name"),
                (lambda: server.cp2k_bench_records("hokusai", system, method, "single_point",
                                                   parsed=parsed), "is run_type right"),
                (lambda: server.cp2k_bench_records("hokusai", {"label": "H2O-32"}, method,
                                                   "aimd_step", parsed=parsed, tag="x"),
                 "missing required atoms")):
            try:
                call()
            except ValueError as e:
                assert why in str(e), (why, e)
            else:
                raise AssertionError(f"cp2k_bench_records did not refuse ({why})")
        assert len(yaml.safe_load((tmp / "benchmarks.yaml").read_text())["records"]) == count + 3
