import json
import os
import tempfile
from pathlib import Path

LABEL = "an old local rsync never stops the tools; the doctor names it"


def old_local_rsync_never_stops_the_tools():
    """AL-14 (2026-11-20): with /usr/bin before Homebrew on the server's PATH,
    remotemanager found macOS's rsync (2.6.9, or openrsync "2.6.9 compatible")
    and refused to build any connection, so every hpc tool failed, not just
    transfers. Now:
    - a newer rsync further down PATH or in a Homebrew folder is used through
      a private folder holding only an `rsync` link (Homebrew's whole bin
      would also swap in its ssh);
    - with none at all, connections still build and transfers use scp;
    - hpc-doctor prints which rsync is on PATH and which the server uses.
    Fake rsync scripts print the real version lines of openrsync and 3.5.1.
    Everything is patched in-process and the link folder is a temp one, so a
    running server's real link is never touched."""
    import contextlib
    import io
    from hpc_agent_core import config, middleware as m
    from hpc_agent_core.doctor import check_local_rsync

    saved = (os.environ.get("PATH"), m.RSYNC_EXTRA_DIRS, m._RSYNC_IN_USE, m.RSYNC_SHIM_DIR)
    slug = "hokusai"
    var = f"{config.get_facility(slug).env_prefix}_CONFIG"
    saved_cfg = os.environ.get(var)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        old, new = d / "old", d / "new"
        for folder, text in ((old, "openrsync: protocol version 29\nrsync version 2.6.9 compatible"),
                             (new, "rsync  version 3.5.1  protocol version 33")):
            folder.mkdir()
            exe = folder / "rsync"
            exe.write_text(f"#!/bin/sh\nprintf '%s\\n' '{text}'\n")
            exe.chmod(0o755)
        cfg = d / "hokusai.json"
        cfg.write_text(json.dumps({"ssh": {"host": "nobody@example.invalid"}}))
        os.environ[var] = str(cfg)
        try:
            m.RSYNC_SHIM_DIR = d / "shim"

            def reset(path, extra):
                os.environ["PATH"] = path
                m.RSYNC_EXTRA_DIRS, m._RSYNC_IN_USE = extra, None

            def doctor_line():
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    assert check_local_rsync() is True     # a WARN, never a FAIL
                return out.getvalue()

            # old rsync first, a modern one in a Homebrew-like folder
            reset(str(old), (str(new),))
            r = m.local_rsync()
            assert r["on_path_version"] == "2.6.9" and r["use"] == str(new / "rsync"), r
            assert r["use_version"] == "3.5.1", r
            line = doctor_line()
            assert "below 3.0" in line and str(new / "rsync") in line, line
            m.get_frontend(slug)                            # used to raise
            assert os.environ["PATH"].split(os.pathsep)[0] == str(d / "shim"), os.environ["PATH"]
            assert sorted(p.name for p in (d / "shim").iterdir()) == ["rsync"]
            assert os.readlink(d / "shim" / "rsync") == str(new / "rsync")
            assert type(m._make_transport(slug)).__name__ == "rsync"

            # no modern rsync anywhere: tools still build, transfers use scp
            reset(str(old), ())
            line = doctor_line()
            assert "no rsync >= 3.0" in line and "scp" in line, line
            m.get_frontend(slug)
            assert type(m._make_transport(slug)).__name__ == "scp"
            import importlib
            assert importlib.import_module("remotemanager.transport.rsync").RSYNC_MIN_VERSION == "3.0.0"

            # a modern one first on PATH: nothing changes
            reset(f"{new}{os.pathsep}{old}", ())
            assert "✓ local rsync" in doctor_line()
            m.get_frontend(slug)
            assert os.environ["PATH"].split(os.pathsep)[0] == str(new)
        finally:
            path, m.RSYNC_EXTRA_DIRS, m._RSYNC_IN_USE, m.RSYNC_SHIM_DIR = saved
            os.environ["PATH"] = path or ""
            if saved_cfg is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = saved_cfg
