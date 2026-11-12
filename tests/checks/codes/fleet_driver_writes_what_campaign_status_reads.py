import os
import pathlib
import signal
import subprocess
import tempfile
import time
from unittest.mock import patch
from checks._common import ROOT, local_run_command

LABEL = 'fleet driver writes what campaign_status reads'

DRIVER = ROOT / "codes" / "common" / "campaign" / "scripts" / "run_fleet.sh"

# stands in for the code: f001 always crashes, f009 runs until stopped,
# the rest write a converged VASP single point
FAKE_RUN = """#!/bin/bash
case "$PWD" in
  */f001) echo "ERROR: fake crash"; exit 3;;
  */f009) sleep 60; exit 1;;
esac
[ -f POTCAR ] || { echo "no POTCAR"; exit 4; }
printf 'DAV:   1   0.1\\n   1 F= -.1E+04 E0= -.1E+04\\n' > OSZICAR
printf ' TOTAL-FORCE (eV/Angst)\\n  free  energy   TOTEN  =     -1000.0 eV\\n' > OUTCAR
echo big > WAVECAR; echo xml > vasprun.xml
echo "ran on $FLEET_NODE with $FLEET_RANKS ranks"
"""


def fleet_driver_writes_what_campaign_status_reads():
    """G33: codes/common/campaign's run_fleet.sh, run locally twice (two
    'submissions') with a fake code, leaves the tree campaign_status reads
    with its default layout: done frames in completed/ (cleaned, gzipped),
    a frame failing twice in failed/ with REASON, a missing shared file
    flagged, a stale claim removed, one try per frame per job, and USR1
    leaving the running frame for the next submission without a try."""
    from hpc_codes import campaign, server
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        root, bindir = tmp / "fleet", tmp / "bin"
        bindir.mkdir()
        (bindir / "fake_run").write_text(FAKE_RUN)
        (bindir / "fake_run").chmod(0o755)
        for f in ("A/f000", "A/f001", "A/f002", "A/f003", "B/f000"):
            (root / f).mkdir(parents=True)
            (root / f / "INCAR").write_text("NELM = 60\n")
        (root / "A" / "POTCAR").write_text("potcar\n")
        # A/f002 finished in an earlier job but was never moved
        (root / "A/f002/OSZICAR").write_text("DAV:   1   0.1\n   1 F= -.1E+04\n")
        (root / "A/f002/OUTCAR").write_text(" TOTAL-FORCE\n  free  energy   TOTEN  = -1.0 eV\n")
        # A/f003 was claimed by a job that is gone
        (root / "A/f003/.running").mkdir()
        (root / "A/f003/.running/jobid").write_text("77\n")

        env = {k: v for k, v in os.environ.items() if not k.startswith("SLURM_")}
        env.update(PATH=f"{bindir}:{env['PATH']}", FLEET_DIR=str(root), FLEET_RUN="fake_run",
                   SLURM_JOB_END_TIME=str(int(time.time()) + 10**6), RANKS_PER_LANE="4")

        def submit(job):
            out = subprocess.run(["bash", str(DRIVER)], env={**env, "SLURM_JOB_ID": job},
                                 capture_output=True, text=True, timeout=60)
            assert out.returncode == 0, out.stderr + out.stdout
            return out.stdout

        def status():
            with patch.object(campaign, "run_command", local_run_command), \
                    patch.dict(os.environ, {"HOKUSAI_HOST": "localhost"}):
                return server.campaign_status("hokusai", str(root))

        log = submit("101")
        assert "removing stale claim A/f003/.running (job 77)" in log, log
        assert "A/f002 already complete, moving" in log, log
        done = root / "completed" / "A" / "f000"
        assert (done / "vasprun.xml.gz").exists() and not (done / "WAVECAR").exists(), sorted(done.iterdir())
        assert not (done / "POTCAR").exists() and not (done / ".running").exists(), sorted(done.iterdir())
        assert "with 4 ranks" in (done / "vasp.out").read_text()
        r = status()
        assert r["counts"] == {"pending": 2, "running": 0, "stale": 0, "done": 3, "failed": 0,
                               "held": 0}, r["counts"]
        att = {a["frame"]: a for a in r["attention"]}
        assert att["B/f000"]["flags"] == ["NEEDS_POTCAR"], att
        assert "A/f001" not in att, att        # one failure so far: not yet many_attempts

        log = submit("101")                    # the same job again: no second try of A/f001
        assert (root / "A/f001/.attempts").read_text().strip() == "1"
        submit("102")
        r = status()
        assert r["counts"]["failed"] == 1 and r["counts"]["pending"] == 1, r["counts"]
        failed = next(a for a in r["attention"] if a["frame"] == "A/f001")
        assert failed["state"] == "failed" and failed["attempts"] == 2 and failed["job_id"] == "102", failed
        assert failed["reason"] == "rc=3 after 2 attempts; see vasp.out*", failed
        assert failed["log_tail"] == "ERROR: fake crash", failed

        # walltime: USR1 stops the running frame and leaves it untried
        (root / "A/f009").mkdir()
        (root / "A/f009/INCAR").write_text("NELM = 60\n")
        p = subprocess.Popen(["bash", str(DRIVER)], env={**env, "SLURM_JOB_ID": "103"},
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(200):
            if (root / "A/f009/OSZICAR").exists() or (root / "A/f009/vasp.out").exists():
                break
            time.sleep(0.05)
        time.sleep(0.3)
        p.send_signal(signal.SIGUSR1)
        out, _ = p.communicate(timeout=30)
        assert "interrupted, leaving for the next submission" in out, out
        assert (root / "A/f009/.attempts").read_text().strip() == "0", out
        assert not (root / "A/f009/.running").exists(), out
        r = status()
        assert r["counts"]["pending"] == 2 and r["counts"]["running"] == 0, r["counts"]
