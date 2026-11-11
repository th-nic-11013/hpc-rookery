from hpc_agent_core.models import JobSpec

LABEL = 'PBS shape and dated machine limits are enforced'
FACILITIES = ('miyabi',)


def miyabi_pbs_shape_and_limits_are_enforced(slug, fac, backend):
    """Keep the offline PBS port honest while Miyabi is inaccessible."""
    if slug != "miyabi":
        return

    spec = JobSpec(
        name="pbs-shape",
        executable="hostname",
        resources={
            "node_count": 2,
            "process_count": 8,
            "cpu_cores_per_process": 2,
            "memory": 3 * 1024**3 + 1,
        },
        attributes={
            "queue_name": "debug-c",
            "account": "caller-project",
            "duration": 300,
        },
    )
    script = backend.render_script(spec)
    assert "#PBS -q debug-c" in script
    assert "#PBS -W group_list=caller-project" in script
    assert "#PBS -l select=2:mpiprocs=4:ompthreads=2:mem=4gb" in script
    assert "#PBS -l walltime=00:05:00" in script
    assert 'cd "$PBS_O_WORKDIR"' in script

    # A PBS directive is a single line of options, so a space inside an
    # argument does not need a newline to inject a second option -- the
    # control-character validator in models.py cannot see this. Both
    # fields are checked against their real syntax instead.
    injections = [
        {"queue_name": "debug-c -l place=excl", "account": "caller-project"},
        {"queue_name": "debug-c", "account": "caller-project -l place=excl"},
        {"queue_name": "debug-c", "account": "grp; qdel 1"},
        {"queue_name": "no-such-queue", "account": "caller-project"},
        {"queue_name": "debug-", "account": "caller-project"},
    ]
    for attrs in injections:
        bad = JobSpec(executable="true", resources={"node_count": 1},
                      attributes={**attrs, "duration": 300})
        try:
            backend.apply_defaults(bad)
            backend.validate_spec(bad)
        except ValueError:
            continue
        raise AssertionError(
            f"miyabi accepted an injectable PBS directive argument: {attrs}"
        )

    # A legitimate path containing a space must survive, quoted, rather
    # than splitting into a second option on the -o/-e directive line.
    spaced = JobSpec(
        name="pbs-spaces", executable="hostname",
        resources={"node_count": 1},
        stdout_path="/home/u/out dir/job.out",
        stderr_path="/home/u/out dir/job.err",
        attributes={"queue_name": "debug-c", "account": "caller-project",
                    "duration": 300},
    )
    backend.apply_defaults(spaced)
    backend.validate_spec(spaced)
    spaced_script = backend.render_script(spaced)
    assert "#PBS -o '/home/u/out dir/job.out'" in spaced_script, spaced_script
    assert "#PBS -e '/home/u/out dir/job.err'" in spaced_script, spaced_script

    finished_without_exit = backend._job({
        "Job_Id": "123.opbs", "job_state": "F",
    })
    assert finished_without_exit.status.state.value == "completed"
    assert finished_without_exit.status.exit_code is None
    failed = backend._job({
        "Job_Id": "124.opbs", "job_state": "F", "Exit_status": "2",
    })
    assert failed.status.state.value == "failed"
    assert failed.status.exit_code == 2
    with_workdir = backend._job({
        "Job_Id": "125.opbs", "job_state": "R",
        "Variable_List": (
            "PBS_O_HOME=/home/u,PBS_O_WORKDIR=/home/u/agent/jobs,"
            "PBS_O_HOST=login"
        ),
    })
    assert with_workdir.status.meta_data["workdir"] == "/home/u/agent/jobs"
    assert backend._pbs_workdir("PBS_O_HOME=/home/u") == ""

    invalid = [
        ("debug-c", {"processes_per_node": 113}),
        ("debug-g", {"processes_per_node": 73}),
        ("debug-mig", {"processes_per_node": 19}),
        ("debug-c", {"gpus": 1}),
        ("debug-g", {"node_count": 2, "gpus": 1}),
    ]
    for queue, resources in invalid:
        bad = JobSpec(
            executable="true",
            resources=resources,
            attributes={"queue_name": queue, "account": "caller-project"},
        )
        try:
            backend.validate_spec(bad)
        except ValueError:
            continue
        raise AssertionError(
            f"Miyabi accepted invalid resources for {queue}: {resources}"
        )
