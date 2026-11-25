import json
import os
import subprocess
import tempfile
from unittest.mock import patch

LABEL = 'a failed SSH call says host down, name unresolved or settings, after a probe'


def ssh_failures_say_down_name_or_settings():
    """2026-11-23: leapfrog (a campus-only 10.x address) was unreachable and
    every call said "the settings there are probably wrong", so the agent
    went to check the config. Reproducing it (2026-11-25) found worse: a host
    that never answers hung ssh past the ~30 s transport wait and read as
    "The cluster was reached ... the command may still be running".

    Now ssh gets ConnectTimeout, and a failed call probes once with the
    facility's own ssh line minus -q (ssh -q prints nothing, so a timeout, a
    bad name and a refused key all arrived as a bare exit 255) and names the
    cause. A probe that connects means the command itself failed: its own
    error comes back, not setup directions; "the cluster was reached" is only
    said after a probe connects. The ssh texts are this Mac's real ones."""
    from hpc_agent_core import config, middleware as m

    class Result:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err

    class Frontend:
        ssh = "ssh -o ConnectTimeout=10 -p 22 -q nobody@10.2.65.226"
        timeout, max_timeouts = 5, 3

        def __init__(self, result=None, exc=None):
            self.result, self.exc = result, exc

        def cmd(self, *a, **k):
            if self.exc:
                raise self.exc
            return self.result

    def probe(rc, err):
        return lambda cmd, **kw: subprocess.CompletedProcess(cmd, rc, "", err)

    DOWN = "ssh: connect to host 10.2.65.226 port 22: Operation timed out"
    NAME = "ssh: Could not resolve hostname leapfrog: nodename nor servname provided, or not known"
    KEY = "nobody@10.2.65.226: Permission denied (publickey)."
    slug = "hokusai"
    var = f"{config.get_facility(slug).env_prefix}_CONFIG"
    previous = os.environ.get(var)
    with tempfile.TemporaryDirectory() as d:
        cfg = os.path.join(d, "hokusai.json")
        json.dump({"ssh": {"host": "nobody@10.2.65.226"}}, open(cfg, "w"))
        os.environ[var] = cfg
        try:
            def run(frontend, probe_fn):
                with patch.object(m, "get_frontend", return_value=frontend), \
                        patch.object(m.subprocess, "run", side_effect=probe_fn) as sp:
                    try:
                        m.run_command(slug, "true")
                    except RuntimeError as e:
                        return str(e), sp
                    raise AssertionError("no error raised")

            msg, sp = run(Frontend(Result(255)), probe(255, DOWN))
            assert "Operation timed out" in msg and "not a settings problem" in msg, msg
            assert "probably wrong" not in msg, msg
            args = sp.call_args.args[0]
            assert "-q" not in args and "BatchMode=yes" in args and args[-1] == "true", args
            assert args.index("nobody@10.2.65.226") > args.index("BatchMode=yes"), args

            msg, _ = run(Frontend(Result(255)), probe(255, NAME))
            assert "does not resolve" in msg and "VPN" in msg, msg

            msg, _ = run(Frontend(Result(255)), probe(255, KEY))
            assert "probably wrong" in msg and "Permission denied (publickey)" in msg, msg

            # the cluster answers: a command that exits 255 by itself keeps its own error
            msg, _ = run(Frontend(Result(255, err="oops")), probe(0, ""))
            assert msg == "oops", msg

            # transport wait ran out: "reached" only after the probe connects
            timeout = RuntimeError("could not communicate with process, killing for safety")
            msg, _ = run(Frontend(exc=timeout), probe(0, ""))
            assert "The cluster was reached" in msg, msg
            msg, _ = run(Frontend(exc=timeout), probe(255, DOWN))
            assert "not a settings problem" in msg and "was reached" not in msg, msg

            # a probe that can't finish counts as down, not as a hang
            def hang(cmd, **kw):
                raise subprocess.TimeoutExpired(cmd, 20)
            msg, _ = run(Frontend(Result(255)), hang)
            assert "not a settings problem" in msg, msg

            # get_frontend adds ConnectTimeout once, and keeps the user's own
            assert f"ConnectTimeout={m.SSH_CONNECT_TIMEOUT_S}" in m.get_frontend(slug).ssh
            json.dump({"ssh": {"host": "nobody@10.2.65.226"},
                       "computer": {"ssh_insert": "-o ConnectTimeout=30"}}, open(cfg, "w"))
            line = m.get_frontend(slug).ssh
            assert "ConnectTimeout=30" in line and line.count("ConnectTimeout") == 1, line
        finally:
            if previous is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = previous
    # classification never reads a scheduler error as an ssh one
    assert m._ssh_failure_kind("Access/permission denied for job 9041823") is None
