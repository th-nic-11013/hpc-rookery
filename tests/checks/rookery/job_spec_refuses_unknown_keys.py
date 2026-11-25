LABEL = 'job specs refuse unknown keys and name where each belongs'


def job_spec_refuses_unknown_keys():
    """AL-18. Pydantic drops unknown keys by default, so job 9895283 (HOKUSAI,
    2026-11-24) was submitted with flat cpus_per_task, nodes, ntasks_per_node,
    partition and time_limit, all silently ignored: no --cpus-per-task, no
    --mem, and Slurm's 1 GB default OOM-killed it. A later preview of the same
    job also had a flat memory key. Replaying the 2026-11-16 run's logged
    specs: 116 of 119 still validate; the 3 refused are this job and its
    previews.

    Every spec model (JobSpec, resources, attributes, container, volume
    mounts) now refuses an unknown key and names its place; resources.memory
    also takes "48G"-style strings (binary units, as --mem reads them); the
    corrected spec renders --cpus-per-task and --mem on HOKUSAI."""
    from hpc_agent_core.models import JobSpec
    import hpc_mcp.hpc_server as server

    flat = {"name": "eval-r2", "directory": "~/agent/work/eval-r2", "executable": "python predict.py",
            "pre_launch": "source env.sh", "partition": "mpc", "nodes": 1, "ntasks_per_node": 1,
            "cpus_per_task": 28, "time_limit": "01:00:00", "memory": "48G"}
    try:
        JobSpec.model_validate(flat)
    except Exception as e:
        msg = str(e)
        for pair in ("'partition' -> attributes.queue_name", "'nodes' -> resources.node_count",
                     "'ntasks_per_node' -> resources.processes_per_node",
                     "'cpus_per_task' -> resources.cpu_cores_per_process",
                     "'time_limit' -> attributes.duration", "'memory' -> resources.memory"):
            assert pair in msg, (pair, msg)
    else:
        raise AssertionError("a flat spec was accepted: its keys would be dropped")

    # nested but misnamed keys are refused too
    for bad, pair in (({"resources": {"cpus_per_task": 28}}, "'cpus_per_task' -> resources.cpu_cores_per_process"),
                      ({"attributes": {"partition": "mpc"}}, "'partition' -> attributes.queue_name"),
                      ({"resources": {"memroy": 1}}, "'memroy'"),
                      ({"container": {"image": "x.sif", "mounts": []}}, "'mounts'")):
        try:
            JobSpec.model_validate({"executable": "x", **bad})
        except Exception as e:
            assert pair in str(e), (bad, str(e))
        else:
            raise AssertionError(f"{bad} was accepted")

    fixed = {"name": "eval-r2", "directory": "~/agent/work/eval-r2", "executable": "python predict.py",
             "pre_launch": "source env.sh",
             "resources": {"node_count": 1, "processes_per_node": 1, "cpu_cores_per_process": 28,
                           "memory": "48G"},
             "attributes": {"queue_name": "mpc", "duration": "01:00:00", "account": "RB000000"}}
    spec = JobSpec.model_validate(fixed)
    assert spec.resources.memory == 48 * 1024 ** 3, spec.resources.memory
    assert JobSpec.model_validate({"executable": "x", "resources": {"memory": 51539607552}}).resources.memory == 48 * 1024 ** 3
    for bad_mem in ("48 apples", "G48", ""):
        try:
            JobSpec.model_validate({"executable": "x", "resources": {"memory": bad_mem}})
        except Exception:
            pass
        else:
            raise AssertionError(f"memory {bad_mem!r} was accepted")
    script = server.render_job_script("hokusai", spec)
    assert "--cpus-per-task=28" in script and "--mem=49152" in script, script
