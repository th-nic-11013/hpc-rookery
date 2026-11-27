import json
import os
import pathlib
import subprocess
import sys
import tempfile
from unittest.mock import patch
from checks._common import local_run_command, no_user_config

LABEL = 'facility_probe and cp2k_check_build report what S0 checked by hand (Python, internet, guard, stack)'


def facility_probe_reports_what_s0_checked_by_hand():
    """The S0 audit's hand checks as tool fields. facility_probe sends one
    read-only command (no redirect into a file, no rm/mkdir) and reports
    the pack's front-end Python (ok from 3.10), the login node's internet
    (an HTTP code; none is a fact, not an error), the submit guard (on/off
    here, the local budget), the scheduler and the pack's launcher lines.
    The SSH layer is stubbed with canned replies, then with a local bash
    whose curl fails, so nothing leaves this machine. cp2k_check_build
    keeps its keys and adds python_*, stack_limit and guard_budget_*."""
    from hpc_codes import probe, server
    from hpc_codes.cp2k import tools

    sent = []

    def reply(text):
        def run(facility, cmd, timeout=None):
            sent.append(cmd)
            return text
        return run

    with tempfile.TemporaryDirectory() as tmp:
        home = pathlib.Path(tmp)
        cfg = home / "hokusai.json"
        cfg.write_text(json.dumps({"ssh": {"host": "hokusai.example"},
                                   "submit_guard": {"budget_core_hours": 250}}))
        env = {k: v for k, v in os.environ.items() if k not in ("HOKUSAI_HOST", "HPC_SUBMIT_GUARD")}
        env.update(HOME=tmp, HOKUSAI_CONFIG=str(cfg), HPC_RECORDS_USER_DIR=str(home / "records"),
                   HPC_SUBMIT_GUARD="1")
        with patch.dict(os.environ, env, clear=True):
            with patch.object(probe, "run_command", reply(
                    "PY_PATH=/usr/bin/python3.12\nPY_VERSION=3.12.4\nNET_CODE=200\n")):
                p = server.facility_probe("hokusai")
            assert len(sent) == 1, sent
            cmd = sent[0]
            assert "/usr/bin/python3.12" in cmd and "curl -sS -m 5" in cmd, cmd
            for bad in ("rm ", "mkdir", "> ~", ">>", "tee ", "touch "):
                assert bad not in cmd, (bad, cmd)
            assert p["python"] == {"command": "/usr/bin/python3.12", "source": "CP2K_PYTHON",
                                   "path": "/usr/bin/python3.12", "version": "3.12.4", "ok": True,
                                   "min": "3.10"}, p["python"]
            assert p["internet"]["http_code"] == "200" and p["internet"]["ok"], p["internet"]
            g = p["guard"]
            assert g["enabled"] and g["budget_core_hours"] == 250 and g["budget_present"], g
            assert g["config_present"] and g["config_path"] == str(cfg) and g["error"] is None, g
            assert p["scheduler"] == "slurm", p
            assert p["launcher"].startswith("srun {CP2K_SRUN_MPI}"), p["launcher"]
            assert p["bench_launcher"].startswith("srun --mpi=pmix --ntasks={R}"), p["bench_launcher"]
            assert p["pack"] is True

            # an old Python, no internet, no budget, guard off: reported, not raised
            cfg.write_text(json.dumps({"ssh": {"host": "hokusai.example"}}))
            os.environ["HPC_SUBMIT_GUARD"] = ""
            with patch.object(probe, "run_command", reply("PY_PATH=/usr/bin/python3\nPY_VERSION=3.6.8\n"
                                                         "NET_CODE=000\n")):
                p = server.facility_probe("hokusai")
            assert p["python"]["ok"] is False and p["python"]["version"] == "3.6.8", p["python"]
            assert p["internet"] == {"url": probe.INTERNET_URL, "http_code": "000", "curl": True,
                                     "ok": False}, p["internet"]
            assert p["guard"]["enabled"] is False and p["guard"]["budget_present"] is False, p["guard"]
            with patch.object(probe, "run_command", reply("PY_PATH=\nNET_CODE=nocurl\n")):
                p = server.facility_probe("hokusai")
            assert p["python"] == {**p["python"], "path": None, "version": None, "ok": False}
            assert p["internet"]["curl"] is False and p["internet"]["http_code"] is None, p["internet"]
            # a facility without the code pack is probed with python3
            with patch.object(probe, "run_command", reply("PY_PATH=/usr/bin/python3\nPY_VERSION=3.11.2\n"
                                                         "NET_CODE=200\n")):
                p = server.facility_probe("hokusai", code="nosuchcode")
            assert p["python"]["command"] == "python3" and p["launcher"] is None and p["pack"] is False, p

            # the real command in a local bash, with a curl that cannot connect
            shims = home / "shims"
            shims.mkdir()
            (shims / "curl").write_text("#!/bin/bash\necho 'curl: (6) Could not resolve host' >&2\n"
                                        "printf 000\nexit 6\n")
            (shims / "curl").chmod(0o755)
            from hpc_codes import packs
            real_values = packs.pack_values
            with patch.object(probe, "run_command", local_run_command), \
                    patch.object(packs, "pack_values", lambda f, c: {**real_values(f, c),
                                                                    "CP2K_PYTHON": sys.executable}), \
                    patch.dict(os.environ, {"PATH": f"{shims}:{os.environ['PATH']}"}):
                p = server.facility_probe("hokusai")
            mine = "%d.%d.%d" % sys.version_info[:3]
            assert p["python"]["version"] == mine and p["python"]["ok"] is (sys.version_info >= (3, 10)), p
            assert p["internet"]["http_code"] == "000" and not p["internet"]["ok"], p["internet"]

            # an unconfigured facility stops before the cluster
            sent.clear()
            with no_user_config("hokusai"), patch.object(probe, "run_command", reply("")):
                try:
                    server.facility_probe("hokusai")
                except RuntimeError as e:
                    assert "not configured yet" in str(e), e
                else:
                    raise AssertionError("facility_probe ran on an unconfigured facility")
            assert not sent, sent

            # cp2k_check_build: its keys, plus the new fields
            fake = home / "build"
            (fake / "bin").mkdir(parents=True)
            (fake / "bin" / "cp2k.psmp").write_text(
                '#!/bin/bash\n[ "$1" = --version ] && { echo " CP2K version 2026.2"; echo " cp2kflags: omp"; }\n')
            (fake / "bin" / "ldd").write_text("#!/bin/bash\ntrue\n")
            for f in (fake / "bin").iterdir():
                f.chmod(0o755)
            (fake / "env.sh").write_text(f"export PATH={fake / 'bin'}:$PATH\n")
            server.records_append("hokusai", "software_registry", {
                "build_id": "cp2k-2026.2-fake", "facility": "hokusai", "code": "cp2k", "version": "2026.2",
                "variant": {"exe": "psmp", "target": "cpu"}, "toolchain": "fake", "install_path": str(fake),
                "env_script": str(fake / "env.sh"), "test_status": {"level": "none", "result": "pass"},
                "default": True, "date": "2026-11-28"})
            cfg.write_text(json.dumps({"ssh": {"host": "hokusai.example"},
                                       "submit_guard": {"budget_core_hours": {"*": 80}}}))
            real_tools_values = tools.pack_values
            with patch.object(tools, "run_command", local_run_command), \
                    patch.object(tools, "pack_values", lambda f, code="cp2k": {
                        **real_tools_values(f, code), "CP2K_PYTHON": sys.executable}):
                b = server.cp2k_check_build("hokusai")
            assert {"ok", "build_id", "env_script_ok", "binary", "version", "cp2kflags", "missing_libs",
                    "data_dir_ok"} <= set(b), b
            assert b["ok"] and b["version"] == "2026.2", b
            assert b["python_version"] == mine and b["python_path"], b
            stack = subprocess.run(["bash", "-c", "ulimit -s"], capture_output=True, text=True).stdout.strip()
            assert b["stack_limit"] == stack, (b["stack_limit"], stack)
            assert b["guard_budget_present"] is True and b["guard_budget_core_hours"] == 80, b
