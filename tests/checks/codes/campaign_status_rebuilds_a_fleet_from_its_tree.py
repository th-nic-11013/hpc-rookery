import os
import pathlib
import tempfile
from unittest.mock import patch
from checks._common import local_run_command, no_user_config

LABEL = 'campaign_status rebuilds a fleet from its tree'


def _tree(root: pathlib.Path) -> dict:
    """Every path under root with its size and mtime, to prove nothing changed."""
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*")}


def campaign_status_rebuilds_a_fleet_from_its_tree():
    """G33: campaign_status's real command, through the local backend, on a
    temporary tree in the fleet driver's layout (codes/common/campaign):
    every state, the attention list and its reasons, the stale claim, the
    file-time wall clock, 2000 frames in one bounded call, a renamed layout,
    a snapshot record, and nothing written on the cluster."""
    from hpc_codes import campaign, records, server

    def must_not_run(*_a, **_k):
        raise AssertionError("an unconfigured facility reached run_command")
    env = {k: v for k, v in os.environ.items() if k != "HOKUSAI_HOST"}
    with patch.dict(os.environ, env, clear=True), no_user_config("hokusai"), \
            patch.object(campaign, "run_command", must_not_run):
        try:
            server.campaign_status("hokusai", "~/agent/work/fleet")
        except RuntimeError as e:
            assert "not configured yet" in str(e), e
        else:
            raise AssertionError("unconfigured campaign_status did not stop")

    calls = []

    def run(facility, cmd, timeout=None):
        calls.append(cmd)
        return local_run_command(facility, cmd, timeout)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        root = tmp / "fleet run"           # a space: the root must be quoted
        bindir = tmp / "bin"
        bindir.mkdir()
        squeue = bindir / "squeue"         # this user's jobs: 222 runs, 333 waits
        squeue.write_text('#!/bin/bash\n[ -n "$FAKE_SQUEUE_FAIL" ] && exit 1\necho 222\necho 333\n')
        squeue.chmod(0o755)

        def frame(path, **files):
            d = root / path
            d.mkdir(parents=True)
            for name, text in files.items():
                (d / {"att": ".attempts", "last": ".lastjob", "log": "vasp.out"}.get(name, name)).write_text(text)
            return d

        frame("Mo2CO2/f000")                                         # pending, never tried
        d = frame("Mo2CO2/f001", att="1\n", last="222\n", INCAR="NELMIN = 4\nNELM = 200\n",
                  OSZICAR="DAV:   1\nDAV:   2\n", log="start\nDAV:   2  -0.1\n\n")
        (d / ".running").mkdir()                                     # running: 222 is queued
        (d / ".running" / "jobid").write_text("222\n")
        d = frame("Mo2CO2/f002", att="1\n", last="111\n")
        (d / ".running").mkdir()                                     # stale: 999 has ended
        (d / ".running" / "jobid").write_text("999\n")
        frame("Mo2CO2/f003", NEEDS_POTCAR="")                       # flagged
        frame("Mo2CO2/f005", att="2\n", last="111\n", log="srun: error: node failure\n")  # retried
        d = frame("completed/Mo2CO2/f004", att="1\n", last="5\n", INCAR="NELM=60\n", OUTCAR="x",
                  OSZICAR="DAV:   1\nRMM:   2\nRMM:   3\n   1 F= -.1E+04 E0= -.1E+04\n",
                  log="should not be read\n")
        os.utime(d / ".lastjob", (1_000_000, 1_000_000))
        os.utime(d / "OUTCAR", (1_003_600, 1_003_600))             # wall_s = 3600
        frame("failed/V2CO2/f010", att="2\n", last="7\n", INCAR="  nelm = 3 ! limit\n",
              REASON="rc=1 after 2 attempts; see vasp.out* / OSZICAR\n",
              OSZICAR="DAV:   1\nDAV:   2\nDAV:   3\n", log="DAV:   3  -0.2\n")
        frame("scf_hold/V2CO2/f011", att="1\n", last="7\n")         # held by hand
        frame("logs/f999")                                           # not a group folder
        frame("hostfiles/f998")
        frame("Mo2CO2/notaframe")

        before = _tree(root)
        envs = {"HPC_RECORDS_USER_DIR": str(tmp / "records"), "HOKUSAI_HOST": "localhost",
                "PATH": f"{bindir}:{os.environ['PATH']}"}
        with patch.object(campaign, "run_command", run), patch.dict(os.environ, envs):
            r = server.campaign_status("hokusai", str(root), record=True, campaign_name="labels")
            assert len(calls) == 1, "more than one remote command"
            assert _tree(root) == before, "campaign_status changed the tree"
            assert r["total"] == 8 and r["left"] == 7, r
            assert r["counts"] == {"pending": 3, "running": 1, "stale": 1, "done": 1,
                                   "failed": 1, "held": 1}, r["counts"]
            assert r["by_group"]["Mo2CO2"]["done_wall_s_median"] == 3600, r["by_group"]
            assert r["by_group"]["V2CO2"] == {"pending": 0, "running": 0, "stale": 0, "done": 0,
                                              "failed": 1, "held": 1}, r["by_group"]
            assert r["queue_read"] and r["warnings"] == [], r
            assert [(a["frame"], a["state"]) for a in r["attention"]] == [
                ("Mo2CO2/f002", "stale"), ("V2CO2/f010", "failed"), ("Mo2CO2/f003", "pending"),
                ("Mo2CO2/f005", "pending"), ("V2CO2/f011", "held")], r["attention"]
            assert r["attention_by_kind"] == {"stale": 1, "failed": 1, "flagged": 1, "retried": 1,
                                              "held": 1}, r["attention_by_kind"]
            stale, failed, flagged, retried, _ = r["attention"]
            assert "job 999" in stale["why"][0], stale
            assert failed["reason"].startswith("rc=1 after 2 attempts") and failed["log_tail"] == "DAV:   3  -0.2", failed
            assert (failed["nscf"], failed["nelm"]) == (3, 3) and "SCF hit its limit (3/3 steps)" in failed["why"], failed
            assert flagged["flags"] == ["NEEDS_POTCAR"], flagged
            assert retried["why"] == ["2 attempts"] and retried["log_tail"] == "srun: error: node failure", retried
            assert r["running"] == [{"frame": "Mo2CO2/f001", "claim_job": "222", "claimed_s_ago": r["running"][0]["claimed_s_ago"],
                                     "attempts": 1, "nscf": 2}], r["running"]

            # the snapshot record: every frame, valid, queryable by frame
            assert records.check("hokusai")["errors"] == []
            snap = server.records_query("hokusai", "campaign", {"campaign": "labels"})
            assert [s["id"] for s in snap] == [r["record_id"]], snap
            frames = snap[0]["frames"]
            assert frames["Mo2CO2/f004"] == {"state": "done", "attempts": 1, "job_id": "5", "nscf": 3,
                                             "nelm": 60, "wall_s": 3600}, frames["Mo2CO2/f004"]
            assert frames["V2CO2/f010"]["reason"].startswith("rc=1"), frames["V2CO2/f010"]
            assert snap[0]["counts"]["stale"] == 1 and snap[0]["layout"] == {}, snap[0]
            hit = server.records_query("hokusai", "campaign", {"frames.Mo2CO2/f002.state": "stale"},
                                       fields=["taken_at"])
            assert len(hit) == 1, hit
            bad = dict(snap[0], id="x", frames={"A/f1": {"state": "lost"}})
            assert any("lost" in e for e in records.errors("hokusai", "campaign", bad)), "bad state accepted"

            # squeue fails: claims can't be judged, so both read as running
            with patch.dict(os.environ, {"FAKE_SQUEUE_FAIL": "1"}):
                r = server.campaign_status("hokusai", str(root))
            assert not r["queue_read"] and r["counts"]["running"] == 2 and r["counts"]["stale"] == 0, r
            assert any("queue was not read" in w for w in r["warnings"]), r["warnings"]

            # a renamed layout: done in ok/, nothing held, no SCF reading
            (root / "completed").rename(root / "ok")
            r = server.campaign_status("hokusai", str(root), layout={
                "done_dir": "ok", "held_dirs": [], "scf_file": None, "attempts_file": ".tries"})
            assert r["counts"]["done"] == 1 and r["counts"]["held"] == 0 and r["total"] == 7, r
            assert not any("attempts" in a for a in r["attention"]), r["attention"]
            assert not any("nscf" in a for a in r["attention"]), r["attention"]
            for bad, why in (({"done": "x"}, "unknown layout key"), ({"reason_file": "a/b"}, "plain name"),
                             ({"done_dir": ""}, "cannot be empty")):
                try:
                    server.campaign_status("hokusai", str(root), layout=bad)
                except ValueError as e:
                    assert why in str(e), e
                else:
                    raise AssertionError(f"layout {bad} accepted")
            try:
                server.campaign_status("hokusai", str(tmp / "absent"))
            except ValueError as e:
                assert "no folder" in str(e), e
            else:
                raise AssertionError("a missing root was accepted")

            # a full campaign: 20 groups x 100 frames, one call, bounded answer
            big = tmp / "big"
            for g in range(20):
                for f in range(100):
                    d = big / ("completed" if f < 60 else "") / f"G{g:02d}" / f"f{f:03d}"
                    d.mkdir(parents=True)
                    (d / ".attempts").write_text("1\n")
                    (d / ".lastjob").write_text("123456\n")
                    (d / "OSZICAR").write_text("DAV:   1\n" * 40)
                    (d / "INCAR").write_text("NELM = 200\n")
            calls.clear()
            r = server.campaign_status("hokusai", str(big), max_frames=5000)
            assert len(calls) == 1 and r["total"] == 2000 and r["counts"]["done"] == 1200, r["counts"]
            out = local_run_command("hokusai", calls[0])
            assert len(out) < 40_000, f"{len(out)} bytes for 2000 frames"
            try:
                server.campaign_status("hokusai", str(big), max_frames=1000)
            except ValueError as e:
                assert "2000 frame folders" in str(e), e
            else:
                raise AssertionError("max_frames was not enforced")
