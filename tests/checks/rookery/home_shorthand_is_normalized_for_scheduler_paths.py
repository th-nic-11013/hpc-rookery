from hpc_agent_core.models import JobSpec

LABEL = 'scheduler directives normalize home-directory shorthand'


def home_shorthand_is_normalized_for_scheduler_paths():
    """Scheduler directives do not expand shell variables themselves.

    The shared submitter starts in $HOME, so every supported home form
    must become a relative path before it reaches a Slurm/GE directive.
    Test both the helper and every currently supported directive path.
    """
    from hpc_agent_core.compute.gridengine import GridEngineBackend
    from hpc_agent_core.compute.slurm import SlurmBackend
    from hpc_agent_core.middleware import norm_path

    expected = {
        "~": ".",
        "~/agent/work": "agent/work",
        "$HOME": ".",
        "$HOME/agent/work": "agent/work",
        "${HOME}/agent/work": "agent/work",
    }
    for source, normalized in expected.items():
        assert norm_path(source) == normalized

    spec = JobSpec(
        executable="true",
        directory="$HOME/agent/work/run",
        stdin_path="${HOME}/inputs/in.dat",
        stdout_path="~/outputs/out.log",
        stderr_path="$HOME/outputs/err.log",
        attributes={"queue_name": "test"},
    )
    slurm = SlurmBackend("test", nodes_always_explicit=True)
    slurm_script = slurm.render_script(spec)
    assert "#SBATCH --chdir=agent/work/run" in slurm_script
    # Slurm resolves these against --chdir, so they are spelled from the
    # run directory to keep their $HOME-relative meaning (G71).
    assert "#SBATCH --input=../../../inputs/in.dat" in slurm_script
    assert "#SBATCH --output=../../../outputs/out.log" in slurm_script
    assert "#SBATCH --error=../../../outputs/err.log" in slurm_script

    ge = GridEngineBackend("test")
    ge_script = ge.render_script(spec)
    assert "#$ -wd agent/work/run" in ge_script
    assert "#$ -o outputs/out.log" in ge_script
    assert "#$ -e outputs/err.log" in ge_script
