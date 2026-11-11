import os
import sys
import tempfile
from unittest.mock import patch
from checks._common import TESTS, local_run_command

LABEL = 'cp2k_kinds reads the build data folder'


def cp2k_kinds_reads_the_build_data_folder():
    """cp2k_kinds' real command against a fixture data folder: q from the
    basis alias, checked against the potentials; a missing alias or
    potential is reported per element; bad input never reaches a shell."""
    from hpc_codes import server
    from hpc_codes.cp2k import tools
    real_pack_values = tools.pack_values
    data = TESTS / "fixtures" / "cp2k_data"
    values = {**tools.pack_values("hokusai"), "CP2K_PYTHON": sys.executable}
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(tools, "run_command", local_run_command), \
            patch.object(tools, "pack_values", lambda f, code="cp2k": (
                values if f == "hokusai" else real_pack_values(f, code))), \
            patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp, "HOKUSAI_HOST": "localhost"}):
        build = {"build_id": "cp2k-2026.2-fake", "facility": "hokusai", "code": "cp2k",
                 "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"},
                 "toolchain": "fake", "install_path": tmp, "env_script": tmp + "/env.sh",
                 "data_dir": str(data), "test_status": {"level": "none", "result": "pass"},
                 "default": True, "date": "2026-11-04"}
        server.records_append("hokusai", "software_registry", build)
        r = server.cp2k_kinds("hokusai", ["Zr", "O", "C", "H", "H", "Xx", "N"])
        assert r["kinds"] == {e: {"q": q, "basis": "DZVP-MOLOPT-SR-GTH", "potential": f"GTH-PBE-q{q}"}
                              for e, q in (("O", 6), ("C", 4), ("H", 1))}, r
        assert set(r["errors"]) == {"Zr", "Xx", "N"}, r
        assert "GTH-PBE-q12" in r["errors"]["Zr"] and "no -qN alias" in r["errors"]["Xx"], r
        assert "no DZVP-MOLOPT-SR-GTH for N" in r["errors"]["N"], r
        assert r["files"] == {"basis": ["BASIS_MOLOPT"], "potential": "GTH_POTENTIALS"}, r
        # UZH: the newest versioned file wins (the unversioned one has no O),
        # q comes from the basis name, the potential must be the same family
        r = server.cp2k_kinds("hokusai", ["Zr", "O", "H"], family="uzh-scan")
        assert r["files"] == {"basis": ["BASIS_MOLOPT_UZH_2026.2"],
                              "potential": "POTENTIAL_UZH_2026.2"}, r
        assert r["kinds"]["Zr"] == {"q": 12, "basis": "DZVP-MOLOPT-SCAN-GTH-q12",
                                    "potential": "GTH-SCAN-q12"}, r
        assert not r["errors"], r
        r = server.cp2k_kinds("hokusai", ["Zr", "O", "H"], family="uzh-pbe0", aux_basis="admm-dzp")
        assert r["files"]["basis"] == ["BASIS_MOLOPT_UZH_2026.2", "BASIS_ADMM_UZH"], r
        assert r["kinds"]["O"]["aux_basis"] == "admm-dzp-q6", r
        assert set(r["errors"]) == {"Zr"} and "admm-dzp-q12" in r["errors"]["Zr"], r
        for kw in ({"family": "gth"}, {"aux_basis": "admm-dzp"}):
            try:
                server.cp2k_kinds("hokusai", ["H"], **kw)
            except ValueError:
                pass
            else:
                raise AssertionError(f"cp2k_kinds accepted {kw}")
        for bad in (["H; rm -rf ~"], []):
            try:
                server.cp2k_kinds("hokusai", bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"cp2k_kinds accepted {bad}")
        try:
            server.cp2k_kinds("hokusai", ["H"], basis="X $(id)")
        except ValueError:
            pass
        else:
            raise AssertionError("cp2k_kinds accepted a shell word as a basis")
