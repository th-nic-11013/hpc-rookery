LABEL = 'file transfers judge by exit status, not stderr'


def file_transfers_judge_by_exit_status():
    """A login-node banner on stderr must not fail an upload or download
    (HOKUSAI prints one on every SSH connection); a non-zero exit must."""
    from types import SimpleNamespace
    from hpc_agent_core.middleware import _transfer

    class Transport:
        def __init__(self, code):
            self.code, self.calls = code, []

        def transfer(self, raise_errors=True):
            self.calls.append(raise_errors)
            return [SimpleNamespace(returncode=self.code, stdout="",
                                    stderr="Welcome to the login node\n")]

    ok = Transport(0)
    _transfer(ok)
    assert ok.calls == [False], ok.calls
    try:
        _transfer(Transport(23))
    except RuntimeError as e:
        assert "exit 23" in str(e) and "Welcome" in str(e), e
    else:
        raise AssertionError("a failed transfer was reported as success")
