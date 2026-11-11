from unittest.mock import patch

LABEL = 'folder upload ships files only and checks each (G6)'


def folder_upload_ships_files_only_and_checks_each():
    """G6: fs_upload takes a folder. The tarball must hold the files
    and folders (an empty one too) but no macOS metadata, caches or
    symlinks; its manifest must hash every file; the cluster-side check
    failing must raise. The transport is stubbed: nothing leaves here."""
    import hashlib
    import tarfile
    import tempfile
    from pathlib import Path
    from hpc_agent_core import middleware

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "run"
        (src / "sub").mkdir(parents=True)
        (src / "empty").mkdir()
        (src / "__pycache__").mkdir()
        (src / "a.inp").write_text("&GLOBAL\n&END GLOBAL\n")
        (src / "sub" / "b.xyz").write_bytes(b"1\n\nH 0 0 0\n")
        (src / "._a.inp").write_bytes(b"\0\5\26\7")
        (src / ".DS_Store").write_bytes(b"x")
        (src / "__pycache__" / "m.pyc").write_bytes(b"x")
        (src / "link").symlink_to("/etc/hosts")
        sent = {}

        def fake_upload(facility, local_path, remote_path):
            with tarfile.open(local_path) as tar:
                sent["members"] = sorted(tar.getnames())
                man = next(m for m in tar.getmembers() if m.name.endswith(".sha256"))
                sent["manifest"] = tar.extractfile(man).read().decode()
            sent["remote"] = remote_path
            return {"remote_path": remote_path, "transport": "stub"}

        def fake_run(facility, command, timeout=None):
            sent["command"] = command
            return sent.get("reply", "ROOKERY_UPLOAD_EXIT=0\n")

        with patch.object(middleware, "upload_file", side_effect=fake_upload), \
                patch.object(middleware, "run_command", side_effect=fake_run):
            r = middleware.upload_dir("hokusai", src, "~/agent/work/run/")
            files = [m for m in sent["members"] if not m.startswith(".rookery-upload-")]
            assert files == ["a.inp", "empty", "sub", "sub/b.xyz"], sent["members"]
            want = {f: hashlib.sha256((src / f).read_bytes()).hexdigest()
                    for f in ("a.inp", "sub/b.xyz")}
            got = dict(reversed(l.split("  ", 1)) for l in sent["manifest"].splitlines())
            assert got == want, got
            assert sent["remote"].startswith("agent/work/run/.rookery-upload-"), sent["remote"]
            assert "sha256sum -c" in sent["command"] and "rm -f" in sent["command"]
            assert r["files"] == 2 and r["verified"] and r["remote_path"] == "agent/work/run", r
            assert sorted(r["skipped"]) == [".DS_Store", "._a.inp", "__pycache__",
                                            "link (symlink)"], r["skipped"]
            sent["reply"] = "sub/b.xyz: FAILED\nROOKERY_UPLOAD_EXIT=1\n"
            try:
                middleware.upload_dir("hokusai", src, "agent/work/run")
            except RuntimeError as e:
                assert "sub/b.xyz: FAILED" in str(e), e
            else:
                raise AssertionError("a failed per-file check did not raise")
