from unittest.mock import patch

LABEL = 'a slow command is not reported as unreachable (G4)'


def slow_commands_are_not_reported_as_unreachable():
    """G4: remotemanager kills a command that outlives timeout x
    max_timeouts and raises "could not communicate with process".
    run_command used to turn that into the SSH setup directions, so a
    slow `cp2k --check` read as a broken connection. It must say the
    command was slow; a real SSH failure keeps the setup text; and a
    per-call timeout reaches remotemanager as an exact wait."""
    from hpc_agent_core import middleware

    class Frontend:
        timeout, max_timeouts = 5, 3

        def __init__(self, exc):
            self.exc, self.kw = exc, None

        def cmd(self, command, **kw):
            self.kw = kw
            raise self.exc

    slow = Frontend(RuntimeError("could not communicate with process"))
    with patch.object(middleware, "get_frontend", return_value=slow):
        try:
            middleware.run_command("hokusai", "sleep 45")
        except RuntimeError as e:
            msg = str(e)
        else:
            raise AssertionError("a killed command did not raise")
    assert "ran longer than the SSH transport waits (about 30 s)" in msg, msg
    assert "probably wrong" not in msg and "submit_job" in msg, msg
    assert "timeout" not in slow.kw, f"no per-call timeout was asked for: {slow.kw}"
    with patch.object(middleware, "get_frontend", return_value=slow):
        try:
            middleware.run_command("hokusai", "sleep 45", timeout=90)
        except RuntimeError as e:
            msg = str(e)
    assert slow.kw.get("timeout") == 90 and slow.kw.get("max_timeouts") == 1, slow.kw
    assert "about 90 s" in msg and "longer timeout to this call" in msg, msg
    assert middleware._transport_wait_s(20, 3) == 120, "20 s x 3 waits 20+20+20+20+40"

    down = Frontend(RuntimeError("ssh: Could not resolve hostname hokusai"))
    with patch.object(middleware, "get_frontend", return_value=down):
        try:
            middleware.run_command("hokusai", "true")
        except RuntimeError as e:
            msg = str(e)
    assert "ran longer" not in msg and "Could not resolve hostname" in msg, msg
