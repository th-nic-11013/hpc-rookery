LABEL = "scheduler errors aren't mistaken for SSH failures"


def unreachable_detection_discriminates():
    """A scheduler error must not be mistaken for an SSH failure.

    Regression guard: a looser version of this check matched a bare
    "permission denied", so Slurm's own "Access/permission denied for
    job 9041823" came back buried in a wall of SSH setup directions
    instead of as the clear scheduler error it is. Caught live while
    testing update_job(hold=True) on HBW2.
    """
    from hpc_agent_core.middleware import _looks_unreachable as unreachable
    scheduler_and_command_errors = [
        ("Access/permission denied for job 9041823", None),
        ("slurm_suspend error:Job has already finished", None),
        ("ls: cannot access '/nope': No such file or directory", None),
        ("sbatch: error: Invalid account or account/partition combination", None),
        ("some remote failure", 2),
    ]
    for text, rc in scheduler_and_command_errors:
        assert not unreachable(text, rc), f"misread as an SSH failure: {text!r}"
    real_ssh_failures = [
        ("user@host: Permission denied (publickey).", None),
        ("ssh: connect to host x port 22: Connection refused", None),
        ("ssh: Could not resolve hostname bad.invalid", None),
        ("", 255),
    ]
    for text, rc in real_ssh_failures:
        assert unreachable(text, rc), f"missed a real SSH failure: {text!r} rc={rc}"
