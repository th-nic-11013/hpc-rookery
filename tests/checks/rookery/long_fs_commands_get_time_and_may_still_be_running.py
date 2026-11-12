from unittest.mock import patch

LABEL = 'long fs_* commands get minutes, and a timeout says the command may still run (G44)'


def long_fs_commands_get_time_and_may_still_be_running():
    """G44: fs_compress over 3400 small files outlived the ~30 s SSH
    transport wait. The call failed while tar went on to finish the
    archive on the cluster, because killing the local SSH client does not
    stop a remote command that writes files. So: the fs_* tools whose run
    time grows with the data ask run_command for minutes; a per-call wait
    never shortens a longer one the user configured; and the timeout
    message says the command may still be running and how to check,
    instead of reading as a failure (or a connection problem)."""
    from hpc_agent_core import middleware
    from hpc_mcp import hpc_server

    asked = {}

    def record(facility, cmd, timeout=None):
        asked[cmd.split()[0]] = timeout
        return ""

    with patch.object(hpc_server, "run_command", record), \
            patch.dict("os.environ", {"HOKUSAI_HOST": "fs-timeout-test.invalid"}):
        hpc_server.fs_compress("hokusai", ["runs"], "runs.tar.gz")
        hpc_server.fs_extract("hokusai", "runs.tar.gz", "out")
        hpc_server.fs_cp("hokusai", "runs", "copy", recursive=True)
        hpc_server.fs_mv("hokusai", "runs", "moved")
        hpc_server.fs_checksum("hokusai", "runs.tar.gz")
        hpc_server.fs_rm("hokusai", "runs/old", recursive=True)
    short = {c: t for c, t in asked.items() if (t or 0) < 300}
    assert len(asked) == 6 and not short, f"no long timeout for: {short or asked}"

    class Frontend:
        def __init__(self, timeout, max_timeouts):
            self.timeout, self.max_timeouts, self.kw = timeout, max_timeouts, None

        def cmd(self, command, **kw):
            self.kw = kw
            raise RuntimeError("could not communicate with process")

    def fail(frontend, timeout):
        with patch.object(middleware, "get_frontend", return_value=frontend):
            try:
                middleware.run_command("hokusai", "tar czf a.tgz runs", timeout=timeout)
            except RuntimeError as e:
                return str(e)
        raise AssertionError("a killed command did not raise")

    msg = fail(Frontend(5, 3), 600)
    assert "about 600 s" in msg, msg
    assert "may still be running" in msg and "ps -u $USER" in msg, msg
    assert "do not re-run" in msg and "probably wrong" not in msg, msg

    patient = Frontend(400, 3)          # the user raised it: 400 x 3 waits 2400 s
    msg = fail(patient, 600)
    assert patient.kw.get("timeout") is None, f"a tool cut the user's wait short: {patient.kw}"
    assert "about 2400 s" in msg, msg
