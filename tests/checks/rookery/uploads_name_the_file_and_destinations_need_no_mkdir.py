import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from checks._common import local_run_command

LABEL = 'fs_upload refuses a folder as remote_path; fs_extract/fs_upload need no fs_mkdir first'


def uploads_name_the_file_and_destinations_need_no_mkdir():
    """L1 bench 2026-11-25 (Nemotron-120B): asked to put a job script in a
    run folder, it passed the folder as remote_path (6 of 10 tries), and
    asked to unpack into a folder it called fs_mkdir instead (8 of 10).
    A folder as remote_path would take the file inside it and the checksum
    would then read a directory; now it is refused before any transfer,
    with the file path to use. fs_extract's default dest_dir is the home
    folder, not the archive's; the description says so."""
    from hpc_mcp import hpc_server
    from hpc_agent_core import middleware

    up, ex, mk = (getattr(hpc_server, n).__doc__ for n in ("fs_upload", "fs_extract", "fs_mkdir"))
    assert "names the file itself" in up and "is refused" in up, up
    assert "no fs_mkdir is needed" in up and "no fs_mkdir is needed" in ex, (up, ex)
    assert "not the\n    archive's folder" in ex, ex
    assert "fs_extract creates" in mk and "fs_upload the folder" in mk, mk

    def no_transfer(_facility):
        raise AssertionError("a refused upload must not start a transfer")

    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(middleware, "run_command", local_run_command), \
            patch.object(middleware, "_make_transport", no_transfer), \
            patch.dict(os.environ, {"HOME": tmp, "HOKUSAI_HOST": "localhost"}):
        job = Path(tmp) / "local" / "a2_job.sh"
        job.parent.mkdir()
        job.write_text("echo hi\n")
        (Path(tmp) / "agent" / "work" / "a2-npt").mkdir(parents=True)
        for remote in ("~/agent/work/a2-npt", "~/agent/work/a2-npt/"):
            try:
                hpc_server.fs_upload("hokusai", str(job), remote)
            except ValueError as e:
                msg = str(e)
            else:
                raise AssertionError(f"uploaded onto the folder {remote}")
            assert "is a folder" in msg and "agent/work/a2-npt/a2_job.sh" in msg, msg
