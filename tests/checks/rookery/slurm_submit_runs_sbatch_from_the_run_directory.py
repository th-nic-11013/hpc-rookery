import os
import pathlib
import subprocess
import tempfile
from unittest.mock import patch

from hpc_agent_core.models import JobSpec

LABEL = 'Slurm submit runs sbatch from spec.directory, so SLURM_SUBMIT_DIR is the run directory'


def slurm_submit_runs_sbatch_from_the_run_directory():
    """Slurm sets SLURM_SUBMIT_DIR to the directory sbatch ran in; --chdir
    moves the job's working directory but leaves SLURM_SUBMIT_DIR alone.
    An existing driver script that starts with `cd "$SLURM_SUBMIT_DIR"`
    (the usual idiom) therefore ran in $HOME when submit_job ran sbatch
    from there (gap G40). Run the real submit command in a local bash with
    a stand-in sbatch that records where it ran and what it was given:
    from the run directory, with --chdir made absolute so the header's
    $HOME-relative --chdir does not resolve against the run directory.
    Without a directory the command is unchanged, and a missing directory
    fails before sbatch runs, naming the directory.
    """
    from hpc_agent_core.compute.slurm import SlurmBackend

    with tempfile.TemporaryDirectory() as tmp:
        home = pathlib.Path(tmp)
        (home / "runs" / "fleet").mkdir(parents=True)
        bin_dir = home / "bin"
        bin_dir.mkdir()
        record = home / "sbatch-call"
        fake = bin_dir / "sbatch"
        fake.write_text(f'#!/bin/bash\n{{ pwd; printf "%s\\n" "$@"; }} > {record}\necho 4242\n')
        fake.chmod(0o755)
        env = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:{os.environ['PATH']}"}

        def run_here(facility, cmd, timeout=None):
            # middleware.run_command's contract: starts in $HOME, stdout
            # back, RuntimeError (with stderr) on a non-zero exit.
            out = subprocess.run(["bash", "-c", 'cd "$HOME" && ' + cmd], env=env,
                                 capture_output=True, text=True, timeout=60)
            if out.returncode:
                raise RuntimeError(out.stderr or out.stdout)
            return out.stdout

        backend = SlurmBackend("test", nodes_always_explicit=True)
        script = "/abs/agent/jobs/fleet-20261112-000000.sh"

        def submit(spec):
            with patch("hpc_agent_core.compute.slurm.write_remote_file", return_value=script), \
                    patch("hpc_agent_core.compute.slurm.run_command", side_effect=run_here) as command:
                return backend.submit(spec), command.call_args.args[1]

        spec = JobSpec(name="fleet", executable="bash", arguments=["fleet.sh"],
                       directory="~/runs/fleet", attributes={"queue_name": "test"})
        assert "#SBATCH --chdir=runs/fleet" in backend.render_script(spec)
        result, _ = submit(spec)
        assert result == {"job_id": "4242", "script_path": script}
        cwd, *args = record.read_text().splitlines()
        run_dir = str((home / "runs" / "fleet").resolve())
        assert os.path.realpath(cwd) == run_dir, (cwd, run_dir)
        assert args[0] == "--parsable" and args[-1] == script, args
        chdir = [a for a in args if a.startswith("--chdir=")]
        assert len(chdir) == 1 and os.path.realpath(chdir[0][len("--chdir="):]) == run_dir, args

        # No directory: sbatch from $HOME exactly as before.
        record.unlink()
        _, command = submit(JobSpec(name="plain", executable="true",
                                    attributes={"queue_name": "test"}))
        assert command == f"sbatch --parsable {script}", command
        assert os.path.realpath(record.read_text().splitlines()[0]) == str(home.resolve())

        # A missing run directory stops before sbatch and says which one.
        record.unlink()
        try:
            submit(JobSpec(name="lost", executable="true", directory="~/runs/missing",
                           attributes={"queue_name": "test"}))
        except RuntimeError as exc:
            assert "runs/missing" in str(exc) and "fs_mkdir" in str(exc), exc
        else:
            raise AssertionError("submit into a missing directory did not fail")
        assert not record.exists(), "sbatch ran although the directory was missing"
