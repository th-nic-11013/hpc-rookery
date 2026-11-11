import os
from unittest.mock import patch

LABEL = 'fs_rm refuses home/root paths'


def fs_rm_refuses_dangerous_paths():
    """fs_rm is the one irreversible tool here — these filesystems have
    no trash. Deleting a home directory or a filesystem root is far more
    likely to be a mistake (an unset variable, a stray default, a glob
    that expanded to nothing) than an intent, so those must be refused
    before any command is built, not just discouraged in a docstring.

    The guard is the code under test, so it can't also be the test's
    safety net: a regression must fail here, never reach a shell. The
    command runner is replaced with one that raises, and the host is a
    non-local, unresolvable name, so even a missed patch goes to a
    failing SSH rather than `rm -r -- ~` on the machine running this."""
    from hpc_mcp.hpc_server import fs_rm

    def must_not_run(facility, command, *args, **kwargs):
        raise AssertionError(
            f"fs_rm built and tried to run {command!r} instead of refusing")

    with patch.dict(os.environ, {"CELL2026_HOST": "fs-rm-test.invalid"}), \
            patch("hpc_mcp.hpc_server.run_command", side_effect=must_not_run):
        for bad in ["", " ", ".", "..", "/", "~", "~/", "*", "$HOME", ".//"]:
            try:
                fs_rm("cell2026", bad)
            except ValueError as e:
                assert "Refusing to delete" in str(e), (
                    f"{bad!r} was not refused before a command was built: {e}")
            else:
                raise AssertionError(f"fs_rm did not refuse {bad!r}")
