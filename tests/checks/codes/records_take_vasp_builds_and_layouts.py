import os
import tempfile
from unittest.mock import patch

LABEL = 'records take VASP builds and per-code layouts (G36)'


def records_take_vasp_builds_and_layouts():
    """G36: a VASP build records its flavour (variant.exe gam) and a VASP
    benchmark its KPAR / NCORE / NPAR in `layout`, so a parallel-layout
    scan keeps what it varied. Both validate and round-trip through
    records_append / records_query in a temporary user scope. The allowed
    values depend on `code` (per_code in the schema): a CP2K record still
    validates, while a CP2K flavour on a VASP build, a VASP flavour on a
    CP2K or LAMMPS build, a VASP layout key on a CP2K benchmark and an
    unknown or mistyped VASP layout key are all refused."""
    from hpc_codes import records, server
    from hpc_codes.records import RecordsError
    build = {"build_id": "vasp-6.4.3-gam-cpu-intel", "facility": "hokusai", "code": "vasp",
             "version": "6.4.3", "variant": {"exe": "gam", "target": "cpu"},
             "toolchain": "Intel oneAPI 2024, Intel MPI, MKL", "install_path": "~/vasp/6.4.3",
             "env_script": "~/vasp/6.4.3/env.sh",
             "test_status": {"level": "smoke", "result": "pass"}, "default": True,
             "date": "2026-11-10"}
    bench = {"id": "hokusai-vasp-2026-11-10-si64-aimdstep-1n-56x1-k2c4",
             "facility": "hokusai", "partition": "mpc", "node_type": "MPC test",
             "code": "vasp", "version": "6.4.3", "build_id": build["build_id"],
             "system": {"label": "Si-64", "atoms": 64,
                        "elements": {"Si": 64},
                        "periodic": "xyz", "kpoints": "gamma"},
             "method": {"functional": "PBE", "basis": "PAW_PBE 54", "cutoff_ry": 38.2,
                        "hybrid": False, "scf": "Normal, EDIFF 1e-5"},
             "run_type": "aimd_step", "nodes": 1, "ranks_per_node": 56, "threads_per_rank": 1,
             "layout": {"KPAR": 2, "NCORE": 4},
             "metric": {"name": "s_per_step", "value": 41.7},
             "date": "2026-11-10", "valid": True}
    cp2k_build = {**build, "build_id": "cp2k-2026.2-psmp-cpu-gcc", "code": "cp2k",
                  "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"}}
    cp2k_bench = {**bench, "id": "hokusai-cp2k-2026-11-10-si64-aimdstep-1n-16x7",
                  "code": "cp2k", "version": "2026.2", "build_id": cp2k_build["build_id"],
                  "method": {**bench["method"], "basis": "DZVP"},
                  "ranks_per_node": 16, "threads_per_rank": 7}
    del cp2k_bench["layout"]
    with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp}):
        for rtype, rec in (("software_registry", build), ("benchmarks", bench),
                           ("software_registry", cp2k_build), ("benchmarks", cp2k_bench)):
            assert not records.errors("hokusai", rtype, rec), records.errors("hokusai", rtype, rec)

        refused = [
            ("software_registry", {**build, "variant": {"exe": "psmp", "target": "cpu"}},
             "variant.exe (code vasp): 'psmp' not in"),
            ("software_registry", {**cp2k_build, "variant": {"exe": "gam", "target": "cpu"}},
             "variant.exe (code cp2k): 'gam' not in"),
            ("software_registry", {**build, "code": "lammps"},
             "variant.exe (code lammps): 'gam' not in enum[other]"),
            ("benchmarks", {**cp2k_bench, "layout": {"KPAR": 2}},
             "layout (code cp2k): unknown field KPAR"),
            ("benchmarks", {**bench, "layout": {"KPAR": 2, "NSIMD": 4}},
             "layout (code vasp): unknown field NSIMD"),
            ("benchmarks", {**bench, "layout": {"KPAR": "2"}},
             "layout (code vasp).KPAR: '2' is not int"),
        ]
        for rtype, rec, why in refused:
            try:
                server.records_append("hokusai", rtype, rec)
            except RecordsError as e:
                assert why in str(e), (why, str(e))
            else:
                raise AssertionError(f"appended a bad record: {why}")
        assert not os.listdir(tmp), "a refused append wrote a file"

        server.records_append("hokusai", "software_registry", build)
        server.records_append("hokusai", "benchmarks", bench)
        server.records_append("hokusai", "software_registry", cp2k_build)
        server.records_append("hokusai", "benchmarks", cp2k_bench)
        got = server.records_query("hokusai", "software_registry", {"variant.exe": "gam"})
        assert [r["build_id"] for r in got] == [build["build_id"]], got
        hits = server.records_query("hokusai", "benchmarks",
                                    {"code": "vasp", "layout.KPAR": 2, "layout.NCORE": "4"},
                                    ["layout", "ranks_per_node"])
        assert hits == [{"id": bench["id"], "_scope": "user", "layout": {"KPAR": 2, "NCORE": 4},
                         "ranks_per_node": 56}], hits
        report = server.records_check("hokusai")
        assert not report["errors"], report["errors"]
