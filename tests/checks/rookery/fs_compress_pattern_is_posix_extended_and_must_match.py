import os
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch
from checks._common import local_run_command

LABEL = 'fs_compress match_pattern is a POSIX extended regex and must match (G43)'


def fs_compress_pattern_is_posix_extended_and_must_match():
    """G43: fs_compress(match_pattern=".*/(INCAR|POSCAR)$") archived
    nothing — find's default regex syntax is emacs, where `(a|b)` is
    literal — and tar wrote an empty 45-byte archive without an error.
    The tool's real command runs here in a local shell on a scratch tree:
    the alternation pattern must select exactly the named files, and a
    pattern that matches nothing must fail, say why, and write no archive."""
    from hpc_mcp import hpc_server

    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(hpc_server, "run_command", local_run_command), \
            patch.dict(os.environ, {"HOKUSAI_HOST": "localhost"}):
        root = Path(tmp) / "runs"
        for run in ("r1", "r2/sub"):
            (root / run).mkdir(parents=True)
            for name in ("INCAR", "POSCAR", "OUTCAR", "WAVECAR"):
                (root / run / name).write_text(name)

        archive = Path(tmp) / "inputs.tar.gz"
        hpc_server.fs_compress("hokusai", [str(root)], str(archive),
                               match_pattern=".*/(INCAR|POSCAR)$")
        with tarfile.open(archive) as tar:
            names = sorted(Path(n).name for n in tar.getnames())
        assert names == ["INCAR", "INCAR", "POSCAR", "POSCAR"], names

        empty = Path(tmp) / "empty.tar.gz"
        try:
            hpc_server.fs_compress("hokusai", [str(root)], str(empty),
                                   match_pattern="INCAR")
        except RuntimeError as e:
            msg = str(e)
        else:
            raise AssertionError("a pattern that matched nothing did not fail")
        assert "no file under" in msg and "'.*/'" in msg, msg
        assert not empty.exists(), "an empty archive was written anyway"
