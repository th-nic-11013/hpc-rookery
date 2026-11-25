import os
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch
from checks._common import local_run_command

LABEL = 'fs_download and fs_compress make their own folders; a local file is not downloaded'


def transfers_make_their_own_folders_and_spot_local_files():
    """Bench v5 (2026-11-25): a small worker called fs_mkdir on a local
    /data folder before fs_download, and fs_mkdir is a cluster tool: a write
    in a read-only task. fs_download already made its local folder; now its
    description says so, and fs_compress makes the archive's folder too. A
    worker also asked to download files it was told are local (L2-S4-06):
    the refusal must say the file is here and needs no download."""
    from hpc_mcp import hpc_server
    from hpc_agent_core import middleware

    for name in ("fs_download", "fs_compress"):
        doc = getattr(hpc_server, name).__doc__
        assert "created if missing" in doc, (name, doc)
    assert "on the cluster" in hpc_server.fs_mkdir.__doc__
    assert "fs_download creates the local" in hpc_server.fs_mkdir.__doc__

    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(hpc_server, "run_command", local_run_command), \
            patch.dict(os.environ, {"HOKUSAI_HOST": "localhost"}):
        src = Path(tmp) / "run"
        src.mkdir()
        (src / "a.out").write_text("a")
        archive = Path(tmp) / "new" / "deeper" / "run.tar.gz"
        hpc_server.fs_compress("hokusai", [str(src / "a.out")], str(archive))
        with tarfile.open(archive) as tar:
            assert [Path(n).name for n in tar.getnames()] == ["a.out"]

        def missing(facility, cmd, timeout=None):
            raise RuntimeError("sha256sum: No such file or directory")

        local = src / "a.out"
        with patch.object(middleware, "run_command", missing):
            try:
                hpc_server.fs_download("hokusai", str(local), str(Path(tmp) / "x" / "a.out"))
            except RuntimeError as e:
                msg = str(e)
            else:
                raise AssertionError("a missing remote file downloaded")
            assert "already" in msg and "no download is needed" in msg, msg
            try:
                hpc_server.fs_download("hokusai", str(Path(tmp) / "nowhere.out"), None)
            except RuntimeError as e:
                assert "No such file" in str(e) and "already" not in str(e), e
            else:
                raise AssertionError("a missing remote file downloaded")
