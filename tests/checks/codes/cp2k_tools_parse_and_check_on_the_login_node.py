import os
import pathlib
import sys
import tempfile
from unittest.mock import patch
from checks._common import ROOT, local_run_command

LABEL = 'cp2k tools parse and check on the login node'


def cp2k_tools_parse_and_check_on_the_login_node():
    """The cp2k_* tools' real shell commands, run against fixtures: two
    scrubbed cp2k.out files from a HOKUSAI smoke stage, and a fake build
    (env script, cp2k.psmp, ldd) in a temporary folder."""
    import shutil
    from hpc_codes import server
    from hpc_codes.cp2k import tools
    real_pack_values = tools.pack_values
    root = ROOT
    stage = root / "tests" / "fixtures" / "cp2k_bench" / "stage1"
    # HOKUSAI's own values, except the login node's Python path
    values = {**tools.pack_values("hokusai"), "CP2K_PYTHON": sys.executable}
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(tools, "run_command", local_run_command), \
            patch.object(tools, "pack_values", lambda f, code="cp2k": (
                values if f == "hokusai" else real_pack_values(f, code))), \
            patch.dict(os.environ, {"HPC_RECORDS_USER_DIR": tmp, "HOKUSAI_HOST": "localhost"}):
        tmp = pathlib.Path(tmp)
        r = server.cp2k_bench_parse("hokusai", [str(stage), str(tmp / "absent")])
        got = r["stages"][str(stage)]
        assert got["passed"] and got["runs"] == 2, got
        assert got["ranked"] == ["8x14", "28x4"], got
        assert all(run["steps"] == 10 and run["dE_max"] < 1e-10 for run in r["runs"]), r["runs"]
        assert r["stages"][str(tmp / "absent")] == {"found": False, "runs": 0,
                                                      "passed": False, "ranked": []}
        broken = tmp / "broken"
        shutil.copytree(stage, broken)
        out = broken / "H2O-32_8x14_r1" / "cp2k.out"
        out.write_text(out.read_text().replace("PROGRAM ENDED AT", "PROGRAM KILLED AT"))
        got = server.cp2k_bench_parse("hokusai", [str(broken)])["stages"][str(broken)]
        assert not got["passed"] and got["ranked"] == ["28x4"], got

        # a fake build: cp2k.psmp and ldd on PATH after the env script
        bin_dir = tmp / "build" / "bin"
        bin_dir.mkdir(parents=True)
        (tmp / "build" / "data").mkdir()
        (bin_dir / "cp2k.psmp").write_text(
            '#!/bin/bash\n'
            'if [ "$1" = --version ]; then echo " CP2K version 2026.2"; echo " cp2kflags: omp mkl"; exit 0; fi\n'
            # what CP2K 2026.2 printed for the input (tests/fixtures/cp2k_echo)
            'if [ "$1" = --echo ]; then [ -f "${3%.inp}.echo" ] && cat "${3%.inp}.echo"; exit 0; fi\n'
            '[ -f "${3%.inp}.abort" ] && { cat "${3%.inp}.abort"; exit 1; }\n'
            'grep -q BAD "$3" && { echo " *** [ABORT] unknown keyword BAD ***"; exit 1; }\n'
            '[ -f sub.inc ] || { echo " *** [ABORT] @INCLUDE sub.inc not found ***"; exit 1; }\n'
            'exit 0\n')
        (bin_dir / "ldd").write_text('#!/bin/bash\n[ -n "$FAKE_MISSING" ] && echo "\tlibxc.so.6 => not found"; true\n')
        # coreutils timeout is on every Linux login node, not on macOS
        (bin_dir / "timeout").write_text('#!/bin/bash\nshift\nexec "$@"\n')
        for f in bin_dir.iterdir():
            f.chmod(0o755)
        env = tmp / "build" / "env.sh"
        env.write_text(f'export PATH={bin_dir}:$PATH\n')
        build = {"build_id": "cp2k-2026.2-fake", "facility": "hokusai", "code": "cp2k",
                 "version": "2026.2", "variant": {"exe": "psmp", "target": "cpu"},
                 "toolchain": "fake", "install_path": str(tmp / "build"),
                 "env_script": str(env), "data_dir": str(tmp / "build" / "data"),
                 "test_status": {"level": "none", "result": "pass"},
                 "default": True, "date": "2026-11-03"}
        server.records_append("hokusai", "software_registry", build)

        res = server.cp2k_check_build("hokusai")
        assert res["ok"] and res["version"] == "2026.2" and res["cp2kflags"] == ["omp", "mkl"], res
        with patch.dict(os.environ, {"FAKE_MISSING": "1"}):
            res = server.cp2k_check_build("hokusai", "cp2k-2026.2-fake")
        assert not res["ok"] and res["missing_libs"] == ["libxc.so.6"], res

        run = tmp / "run dir"         # a space: the folder must be quoted
        run.mkdir()
        (run / "sub.inc").write_text("")
        (run / "good.inp").write_text("&GLOBAL\n&END\n")
        (run / "bad.inp").write_text("BAD\n")
        res = server.cp2k_check_input("hokusai", str(run / "good.inp"))
        assert res["ok"] and res["build_id"] == "cp2k-2026.2-fake", res
        assert res["files"] == [] and res["warnings"] == [], res
        res = server.cp2k_check_input("hokusai", str(run / "bad.inp"))
        assert not res["ok"] and any("unknown keyword BAD" in e for e in res["errors"]), res

        # the files the input reads at run time (G23), from CP2K's own
        # --echo of cp2k_make_input-shaped inputs; existence is tested
        # for real, from the input's folder
        echo_src = root / "tests" / "fixtures" / "cp2k_echo"
        wfn_run = tmp / "wfn run"
        wfn_run.mkdir()
        for f in echo_src.iterdir():
            if f.suffix in (".inp", ".inc", ".echo"):
                shutil.copy(f, wfn_run / f.name)
        (wfn_run / "sub.inc").write_text("")
        check = lambda name: server.cp2k_check_input("hokusai", str(wfn_run / name))

        res = check("atomic.inp")       # SCF_GUESS ATOMIC: nothing to read
        assert res["ok"] and res["files"] == [] and res["warnings"] == [], res
        assert not any(l.startswith("HPC_ECHO") for l in res["tail"]), res["tail"]

        guess = "../pbe-guess/pbe-guess-RESTART.wfn"
        res = check("guess_wfn.inp")    # RESTART 0: the @SET WFN guess, missing
        assert res["ok"], res           # a missing wfn warns; it does not fail the parse
        assert res["files"] == [{"keyword": "WFN_RESTART_FILE_NAME", "path": guess,
                                 "exists": False}], res
        assert len(res["warnings"]) == 1 and guess in res["warnings"][0] \
            and "atomic guess" in res["warnings"][0], res
        (tmp / "pbe-guess").mkdir()
        (tmp / "pbe-guess" / "pbe-guess-RESTART.wfn").write_text("")
        res = check("guess_wfn.inp")    # present: no warning
        assert res["ok"] and res["warnings"] == [], res
        assert res["files"] == [{"keyword": "WFN_RESTART_FILE_NAME", "path": guess,
                                 "exists": True}], res

        res = check("restart1.inp")     # RESTART 1: the @IF branch's own files
        assert res["ok"], res
        assert res["files"] == [
            {"keyword": "EXT_RESTART/RESTART_FILE_NAME", "path": "hse06-geo-1.restart", "exists": True},
            {"keyword": "WFN_RESTART_FILE_NAME", "path": "hse06-geo-RESTART.wfn", "exists": False}], res
        assert len(res["warnings"]) == 1 and "hse06-geo-RESTART.wfn" in res["warnings"][0], res
        (wfn_run / "hse06-geo-RESTART.wfn").write_text("")
        res = check("restart1.inp")
        assert res["ok"] and res["warnings"] == [] and res["files"][1]["exists"], res

        res = check("restart_noguess.inp")   # no WFN keyword: CP2K's default name
        assert res["ok"], res
        assert [(f["path"], f["exists"]) for f in res["files"]] == [
            ("pbe-geo-1.restart", True), ("pbe-geo-RESTART.wfn", False)], res
        assert "default" in res["files"][1]["keyword"] and len(res["warnings"]) == 1, res

        # the .restart itself missing: --check already stops on it
        shutil.copy(wfn_run / "restart1.inp", wfn_run / "restart1_gone.inp")
        shutil.copy(echo_src / "restart1.abort", wfn_run / "restart1_gone.abort")
        res = check("restart1_gone.inp")
        assert not res["ok"], res
        assert res["errors"][0].startswith("[ABORT] The specified file <hse06-geo-1.restart>"), res
        assert res["files"] == [{"keyword": "EXT_RESTART/RESTART_FILE_NAME",
                                 "path": "hse06-geo-1.restart", "exists": False}], res
        assert len(res["warnings"]) == 1 and "RESTART 0" in res["warnings"][0], res

        # a long name is hard-wrapped over CP2K's box lines (2026.2 output)
        stars = " " + "*" * 79
        wrapped = [stars,
                   " * [ABORT]                          The specified file                         *",
                   " *  \\___/   <../a-rather-long-folder-name/and-a-long-project-name-for-wrapping *",
                   " *    |       -tests-1.restart> cannot be opened with STATUS=OLD. It does not  *",
                   " *  O/|           exist. Data directory path: /usr/local/share/cp2k/data       *",
                   " * / \\                                                   common/cp_files.F:441 *",
                   stars, "            2 handle_ext_restart"]
        long_name = "../a-rather-long-folder-name/and-a-long-project-name-for-wrapping-tests-1.restart"
        assert tools._abort_missing_file(wrapped) == long_name
        assert tools._abort_missing_file(wrapped[:-1]) is None   # not the EXT_RESTART handler
        info = [stars, " *                            RESTART INFORMATION                              *", stars,
                " *    RESTART FILE NAME: ../a-rather-long-folder-name/and-a-long-project-name- *",
                " *                       for-wrapping-tests-1.restart                          *",
                " *                                                                             *",
                " * RESTARTED QUANTITIES:                                                       *",
                " *                       CELL                                                  *", stars]
        assert tools._ext_restart_read(info) == long_name

        # a removed default is never used; an unknown id lists what exists
        server.records_append("hokusai", "software_registry",
                              {**build, "build_id": "cp2k-gone", "removed": "2026-11-03"})
        for bad in ("cp2k-gone", "no-such-build"):
            try:
                server.cp2k_check_build("hokusai", bad)
            except ValueError as e:
                assert "cp2k-2026.2-fake" in str(e) and "cp2k-gone" not in str(e).split("Available:")[1], e
            else:
                raise AssertionError(f"used build {bad}")
        try:
            server.cp2k_check_build("rikyu")
        except ValueError as e:
            assert "has not opted into the cp2k code pack" in str(e), e
        else:
            raise AssertionError("cp2k tool ran on a facility without the pack")
