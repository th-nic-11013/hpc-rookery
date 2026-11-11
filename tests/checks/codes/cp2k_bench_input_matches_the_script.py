import os
import pathlib
import subprocess
import sys
import tempfile
from unittest.mock import patch
from checks._common import ROOT, local_run_command

LABEL = 'cp2k_bench_input matches the script'


def cp2k_bench_input_matches_the_script():
    """cp2k_bench_input writes what make_cp2k_md_input.py (the by-hand
    fallback) writes for the same structure and data folder, apart from
    the "Written by" line; kinds come through cp2k_kinds' real command;
    a missing kind or an existing input stops it before writing."""
    from hpc_codes import server
    from hpc_codes.cp2k import tools
    real_pack_values = tools.pack_values
    root = ROOT
    script = root / "codes" / "cp2k" / "benchmark" / "scripts" / "make_cp2k_md_input.py"
    fx = root / "tests" / "fixtures" / "structures"
    data = root / "tests" / "fixtures" / "cp2k_data"
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
        tmp = pathlib.Path(tmp)
        cases = [("clean.xyz", {}), ("ch2o.xyz", {"xc": "PBE", "d3": "bj", "charge": 1,
                                                     "steps": 20, "cutoff": 500})]
        for xyz, kw in cases:
            old, new = tmp / f"old-{xyz}", tmp / f"new-{xyz}"
            flags = [f"--{k.replace('_', '-')}={v}" for k, v in kw.items()]
            subprocess.run([sys.executable, "-I", str(script), str(fx / xyz), "--name", "sys",
                            "--data-dir", str(data), "--out-dir", str(old), *flags],
                           check=True, capture_output=True, timeout=60)
            r = server.cp2k_bench_input("hokusai", str(fx / xyz), "sys", str(new), **kw)
            assert r["inputs"] == [str(new / "sys.inp"), str(new / "sys-smoke.inp")], r
            for f in ("sys.inp", "sys-smoke.inp"):
                a, b = (old / f).read_text().splitlines(), (new / f).read_text().splitlines()
                assert a[:2] + a[3:] == b[:2] + b[3:], (xyz, f, a, b)
            assert r["build_id"] == "cp2k-2026.2-fake", r
        assert r["electrons"] == 11 and r["formula"] == "C1O1H2", r
        assert len(r["warnings"]) == 1 and "odd" in r["warnings"][0], r
        r = server.cp2k_bench_input("hokusai", str(fx / "ch2o.xyz"), "even", str(tmp / "e"))
        assert r["electrons"] == 12 and r["warnings"] == [] and min(r["widths"]) > 10, r
        assert any("below 10 A" in w for w in
                   server.cp2k_bench_input("hokusai", str(fx / "clean.xyz"), "t", str(tmp / "t"))["warnings"])
        zr = tmp / "zr.xyz"
        zr.write_text((fx / "clean.xyz").read_text().replace("\nO ", "\nZr "))
        for args, kw, text in (((str(zr), "z", str(tmp / "z")), {}, "GTH-PBE-q12"),
                               ((str(fx / "clean.xyz"), "sys", str(tmp / "new-clean.xyz")), {}, "exist"),
                               ((str(fx / "clean.xyz"), "a b", str(tmp / "n")), {}, "name"),
                               ((str(fx / "clean.xyz"), "x", str(tmp / "x")), {"d3": "d4"}, "d3"),
                               ((str(tmp / "absent.xyz"), "x", str(tmp / "x")), {}, "not a file")):
            try:
                server.cp2k_bench_input("hokusai", *args, **kw)
            except ValueError as e:
                assert text in str(e), e
            else:
                raise AssertionError(f"cp2k_bench_input accepted {args[:2]} {kw}")
        assert not (tmp / "z").exists() and not (tmp / "x").exists()
