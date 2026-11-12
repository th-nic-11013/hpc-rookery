import os
import pathlib
import subprocess
import tempfile
from unittest.mock import patch

from hpc_agent_core.models import JobSpec

LABEL = 'Slurm writes a $HOME-relative stdout/stderr (and reads stdin) under $HOME, not inside spec.directory'


def _where_slurm_puts(cwd: str, args: list[str], script: str) -> dict:
    """Resolve a job's stdin/stdout/stderr the way Slurm does: options on
    the sbatch command line override the script's #SBATCH lines, sbatch
    makes a relative --chdir absolute against the directory it runs in,
    and slurmstepd prefixes a relative --input/--output/--error with that
    working directory before opening it (so '..' is resolved by the
    filesystem, through any symlink)."""
    opts = {}
    for line in script.splitlines():
        if line.startswith("#SBATCH --"):
            key, _, value = line[len("#SBATCH --"):].partition("=")
            opts[key] = value
    for arg in args[:-1]:
        if arg.startswith("--") and "=" in arg:
            key, _, value = arg[2:].partition("=")
            opts[key] = value
    workdir = os.path.join(cwd, opts.get("chdir", "."))
    return {key: os.path.realpath(opts[key] if opts[key].startswith("/") else f"{workdir}/{opts[key]}")
            for key in ("input", "output", "error") if key in opts}


def slurm_home_relative_job_paths_stay_home_relative_with_a_run_directory():
    """A relative path in a spec is $HOME-relative (norm_path turns ~/x
    into x for that reason), but Slurm resolves a relative --output,
    --error or --input against the job's working directory, which --chdir
    sets to spec.directory. A spec with both a directory and ~/logs/x.out
    therefore wrote <directory>/logs/x.out (gap G71). Render and "submit"
    such specs through a stand-in sbatch in a local bash, resolve the
    paths as Slurm would, and require the $HOME-relative location: for
    submit_job, for the saved script resubmitted by hand from $HOME, with
    a symlinked run directory, and with an absolute run directory.
    """
    from hpc_agent_core.compute.slurm import SlurmBackend

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp).resolve()
        home = root / "home"
        for sub in ("runs/fleet", "logs", "inputs", "agent/jobs", "bin"):
            (home / sub).mkdir(parents=True)
        (root / "elsewhere" / "runs" / "linked").mkdir(parents=True)
        (home / "linked").symlink_to(root / "elsewhere" / "runs")
        (root / "abs" / "run").mkdir(parents=True)
        record = root / "sbatch-call"
        fake = home / "bin" / "sbatch"
        fake.write_text(f'#!/bin/bash\n{{ pwd -P; printf "%s\\n" "$@"; }} > {record}\necho 4242\n')
        fake.chmod(0o755)
        env = {**os.environ, "HOME": str(home), "PATH": f"{home / 'bin'}:{os.environ['PATH']}"}

        def run_here(facility, cmd, timeout=None):
            # middleware.run_command's contract: starts in $HOME, stdout
            # back, RuntimeError (with stderr) on a non-zero exit.
            out = subprocess.run(["bash", "-c", 'cd "$HOME" && ' + cmd], env=env,
                                 capture_output=True, text=True, timeout=60)
            if out.returncode:
                raise RuntimeError(out.stderr or out.stdout)
            return out.stdout

        def save(facility, path, content):
            saved = home / path
            saved.write_text(content)
            return str(saved)

        backend = SlurmBackend("test", nodes_always_explicit=True)

        def submit(spec):
            with patch("hpc_agent_core.compute.slurm.write_remote_file", side_effect=save), \
                    patch("hpc_agent_core.compute.slurm.run_command", side_effect=run_here):
                result = backend.submit(spec)
            cwd, *args = record.read_text().splitlines()
            script = pathlib.Path(result["script_path"]).read_text()
            return _where_slurm_puts(cwd, args, script), result["script_path"]

        def by_hand(script_path):
            # the saved script resubmitted as `sbatch <script>` from $HOME
            run_here("test", f"sbatch {script_path}")
            cwd, *args = record.read_text().splitlines()
            return _where_slurm_puts(cwd, args, pathlib.Path(script_path).read_text())

        want = {"input": str(home / "inputs/fleet.in"), "output": str(home / "logs/fleet.out"),
                "error": str(home / "logs/fleet.err")}

        def spec(directory, name="fleet"):
            return JobSpec(name=name, executable="true", directory=directory,
                           stdin_path="inputs/fleet.in", stdout_path="~/logs/fleet.out",
                           stderr_path="$HOME/logs/fleet.err", attributes={"queue_name": "test"})

        # Run directory under $HOME: submit_job and a hand resubmission agree.
        where, saved = submit(spec("~/runs/fleet"))
        assert where == want, (where, saved)
        assert "#SBATCH --output=../../logs/fleet.out" in pathlib.Path(saved).read_text()
        assert by_hand(saved) == want, by_hand(saved)

        # A symlinked run directory: '..' climbs the link's target, so only
        # the command line's "$HOME"/... keeps submit_job right.
        where, saved = submit(spec("~/linked/linked", name="linked"))
        assert where == want, (where, saved)

        # An absolute run directory has no $HOME-relative spelling at all.
        where, saved = submit(spec(str(root / "abs" / "run"), name="abs"))
        assert where == want, (where, saved)

        # Absolute paths are left alone, and not repeated on the command line.
        absolute = JobSpec(name="absolute", executable="true", directory="~/runs/fleet",
                           stdout_path=str(root / "abs" / "out.log"),
                           attributes={"queue_name": "test"})
        where, _ = submit(absolute)
        assert where == {"output": str(root / "abs" / "out.log")}, where
        assert not any(a.startswith("--output") for a in record.read_text().splitlines()), \
            record.read_text()
        # A path inside the run directory is spelled as it is seen from there.
        inside = JobSpec(name="inside", executable="true", directory="~/runs/fleet",
                         stdout_path="~/runs/fleet/%j.out", attributes={"queue_name": "test"})
        assert "#SBATCH --output=%j.out" in backend.render_script(inside)
