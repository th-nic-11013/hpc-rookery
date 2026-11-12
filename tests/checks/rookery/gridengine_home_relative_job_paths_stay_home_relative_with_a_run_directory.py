import os
import pathlib
import shlex
import subprocess
import tempfile
from unittest.mock import patch

from hpc_agent_core.models import JobSpec

LABEL = 'Grid Engine writes a $HOME-relative stdout/stderr under $HOME, not inside spec.directory'

# Grid Engine's pseudo environment variables in -o/-e/-i path names,
# expanded when the job starts (qsub(1)); $HOME is the execution host's.
_JOB_ID = "4242"


def _where_ge_puts(cwd: str, args: list[str], script: str, home: str) -> dict:
    """Resolve a job's stdout/stderr the way Grid Engine does: options on
    the qsub command line override the script's '#$' lines, the working
    directory is -wd (relative to the submit directory) or, with -cwd, the
    submit directory itself, the pseudo variables in -o/-e are expanded,
    and a path still relative is opened in the working directory (so '..'
    and symlinks are resolved by the filesystem)."""
    opts: dict[str, str | None] = {}

    def take(tokens):
        i = 0
        while i < len(tokens):
            flag = tokens[i]
            if flag in ("-wd", "-o", "-e", "-i", "-N"):
                opts[flag] = tokens[i + 1]
                i += 2
            elif flag == "-cwd":
                opts["-wd"] = None
                i += 1
            else:
                i += 1

    for line in script.splitlines():
        if line.startswith("#$ "):
            take(shlex.split(line[3:]))
    take(args[:-1])
    workdir = cwd if opts.get("-wd") is None else os.path.join(cwd, opts["-wd"])
    variables = {"$HOME": home, "$JOB_ID": _JOB_ID, "$JOB_NAME": opts.get("-N") or ""}
    where = {}
    for flag, key in (("-o", "output"), ("-e", "error"), ("-i", "input")):
        if flag not in opts:
            continue
        path = opts[flag]
        for name, value in variables.items():
            path = path.replace(name, value)
        where[key] = os.path.realpath(path if path.startswith("/") else f"{workdir}/{path}")
    return where


def gridengine_home_relative_job_paths_stay_home_relative_with_a_run_directory():
    """A relative path in a spec is $HOME-relative, but qsub resolves a
    relative -o/-e against the job's working directory, which -wd sets to
    spec.directory, so ~/logs/x.out landed in <directory>/logs/x.out, and
    the generic backend's default ~/agent/jobs/<name>.o with it (gap G71).
    Render and "submit" such specs through a stand-in qsub in a local bash
    for both Grid Engine backends (the generic one and TSUBAME's), resolve
    the paths as Grid Engine would, and require the $HOME-relative
    location: for submit_job, for the saved script resubmitted by hand
    from $HOME and from elsewhere, with a symlinked run directory and with
    an absolute one. Absolute paths and specs without a directory keep
    their old spelling.
    """
    from facilities.tsubame.compute import TsubameBackend
    from hpc_agent_core.compute.gridengine import GridEngineBackend

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp).resolve()
        home = root / "home"
        for sub in ("runs/fleet", "logs", "agent/jobs", "bin"):
            (home / sub).mkdir(parents=True)
        (root / "elsewhere" / "runs" / "linked").mkdir(parents=True)
        (home / "linked").symlink_to(root / "elsewhere" / "runs")
        (root / "abs" / "run").mkdir(parents=True)
        record = root / "qsub-call"
        fake = home / "bin" / "qsub"
        fake.write_text(
            f'#!/bin/bash\n{{ pwd -P; printf "%s\\n" "$@"; }} > {record}\n'
            f'case " $* " in *" -terse "*) echo {_JOB_ID};; '
            f'*) echo "Your job {_JOB_ID} (\\"job\\") has been submitted";; esac\n')
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

        def resolved():
            cwd, *args = record.read_text().splitlines()
            return cwd, args

        backends = {
            "gridengine": (GridEngineBackend("test"), "hpc_agent_core.compute.gridengine",
                           {"queue_name": "all.q"}),
            "tsubame": (TsubameBackend("tsubame"), "facilities.tsubame.compute",
                        {"duration": 60, "account": "grp", "queue_name": ""}),
        }
        want = {"output": str(home / "logs/fleet.out"), "error": str(home / "logs/fleet.err")}

        for label, (backend, module, attributes) in backends.items():
            def submit(spec):
                with patch(f"{module}.write_remote_file", side_effect=save), \
                        patch(f"{module}.run_command", side_effect=run_here):
                    result = backend.submit(spec)
                cwd, args = resolved()
                script = pathlib.Path(result["script_path"]).read_text()
                return _where_ge_puts(cwd, args, script, str(home)), result["script_path"]

            def by_hand(script_path, start="."):
                # the saved script resubmitted as `qsub <script>` by hand
                run_here("test", f"cd {shlex.quote(start)} && qsub {shlex.quote(script_path)}")
                cwd, args = resolved()
                return _where_ge_puts(cwd, args, pathlib.Path(script_path).read_text(), str(home))

            def spec(directory, name="fleet", **paths):
                paths = paths or {"stdout_path": "~/logs/fleet.out",
                                  "stderr_path": "$HOME/logs/fleet.err"}
                return JobSpec(name=name, executable="true", directory=directory,
                               attributes=attributes, **paths)

            # Run directory under $HOME: submit_job and hand resubmissions agree.
            where, saved = submit(spec("~/runs/fleet"))
            assert where == want, (label, where, pathlib.Path(saved).read_text())
            assert "#$ -o $HOME/logs/fleet.out" in pathlib.Path(saved).read_text(), label
            assert by_hand(saved) == want, (label, by_hand(saved))
            assert by_hand(saved, "runs") == want, (label, by_hand(saved, "runs"))

            # A symlinked run directory, and an absolute one.
            where, _ = submit(spec("~/linked/linked", name="linked"))
            assert where == want, (label, where)
            where, _ = submit(spec(str(root / "abs" / "run"), name="abs"))
            assert where == want, (label, where)

            # Absolute paths are left alone, and nothing goes on the command line.
            where, saved = submit(spec("~/runs/fleet", name="absolute",
                                       stdout_path=str(root / "abs" / "out.log"),
                                       stderr_path=str(root / "abs" / "err.log")))
            assert where == {"output": str(root / "abs" / "out.log"),
                             "error": str(root / "abs" / "err.log")}, (label, where)
            assert not {"-o", "-e"} & set(resolved()[1]), (label, resolved())

            # Without a run directory the job starts in $HOME (-cwd): unchanged.
            where, saved = submit(spec(None, name="plain"))
            assert where == want, (label, where)
            assert "#$ -o logs/fleet.out" in pathlib.Path(saved).read_text(), label

        # The generic backend's default ~/agent/jobs/<name>.o/.e is
        # $HOME-relative too.
        generic, module, attributes = backends["gridengine"]
        with patch(f"{module}.write_remote_file", side_effect=save), \
                patch(f"{module}.run_command", side_effect=run_here):
            result = generic.submit(JobSpec(name="dflt", executable="true",
                                            directory="~/runs/fleet", attributes=attributes))
        cwd, args = resolved()
        where = _where_ge_puts(cwd, args, pathlib.Path(result["script_path"]).read_text(), str(home))
        assert where == {"output": str(home / "agent/jobs/dflt.o"),
                         "error": str(home / "agent/jobs/dflt.e")}, where
