import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time

from hpc_agent_core.models import JobSpec

LABEL = 'exec_command lets a signal sent to the batch shell reach the command'

# The job's program: note SIGUSR1 in a file, say it is ready, then wait.
_PROGRAM = """
import pathlib, signal, sys, time
out = pathlib.Path(sys.argv[1])
signal.signal(signal.SIGUSR1, lambda *_: (out.write_text("wound down"), sys.exit(0)))
pathlib.Path(sys.argv[2]).touch()
time.sleep(30)
"""


def _signal_batch_shell(spec: JobSpec, tmp: pathlib.Path) -> str:
    """Run the rendered Slurm script in a local bash (its #SBATCH lines are
    comments there), send SIGUSR1 to that bash as `--signal=B:USR1@N` does,
    and return what the program recorded ("" if it never saw the signal)."""
    from hpc_agent_core.compute.slurm import SlurmBackend

    script = tmp / "job.sh"
    script.write_text(SlurmBackend("test", nodes_always_explicit=True).render_script(spec))
    out, ready = tmp / "out", tmp / "ready"
    for f in (out, ready):
        f.unlink(missing_ok=True)
    shell = subprocess.Popen(["bash", str(script)], cwd=tmp,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 20
        while not ready.exists():
            assert time.time() < deadline, "job program never started"
            assert shell.poll() is None, "batch shell exited before the program started"
            time.sleep(0.05)
        os.kill(shell.pid, signal.SIGUSR1)
        shell.wait(timeout=20)
        time.sleep(0.2)  # an orphaned program would still be running; let it settle
        return out.read_text() if out.exists() else ""
    finally:
        if shell.poll() is None:
            shell.kill()
        # a program left behind by the old wrapper keeps its own process
        subprocess.run(["pkill", "-f", str(ready)], check=False)


def exec_command_lets_a_batch_shell_signal_reach_the_command():
    """Slurm's `--signal=B:USR1@N` (a graceful wind-down before the time
    limit) signals the batch shell only. rookery's script ran the command
    as a child of that shell, which has no trap and just died, so a
    restartable driver never saw the signal (gap G40). With
    JobSpec.exec_command the command replaces the shell and gets it; without
    it the old behaviour stays. Also: exec_command refuses specs where
    `exec` would silently drop work (post_launch, `a && b`, pipelines) and
    accepts redirections and quoted operators.
    """
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        base = dict(name="fleet", executable=sys.executable,
                    arguments=["-c", _PROGRAM, str(tmp / "out"), str(tmp / "ready")],
                    attributes={"queue_name": "test",
                                "custom_attributes": {"signal": "B:USR1@300"}})
        spec = JobSpec(**base, exec_command=True)
        assert _signal_batch_shell(spec, tmp) == "wound down"
        assert _signal_batch_shell(JobSpec(**base), tmp) == "", \
            "without exec_command the plain batch shell should still swallow the signal"

    def refused(**fields):
        try:
            JobSpec(attributes={"queue_name": "test"}, exec_command=True, **fields)
        except ValueError as exc:
            return str(exc)
        return ""

    assert "post_launch" in refused(executable="./fleet.sh", post_launch="echo done")
    for compound in ("module load x && ./app", "cd run; ./app", "./app | tee log",
                     "./app &", "./a || ./b", "./app\n./other"):
        assert "pre_launch" in refused(executable=compound), compound
    for single in ("./app > log 2>&1", "bash -c 'a; b && c'", "./app &> log",
                   'srun ./app "x|y"'):
        assert refused(executable=single) == "", single
