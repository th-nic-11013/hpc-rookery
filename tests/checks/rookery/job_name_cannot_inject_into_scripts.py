LABEL = 'job names cannot inject into scripts'


def job_name_cannot_inject_into_scripts():
    """spec.name is rendered unquoted into every scheduler's directive
    line and used to build the job script's filename, so it is a real
    injection boundary — a newline puts executable lines into a script
    the scheduler runs, and a slash writes the script outside the jobs
    directory. Both were demonstrated against this code. Guarded once in
    the model so it holds for schedulers added later.
    """
    from hpc_agent_core.models import JobSpec
    for bad in ["ok\nrm -rf /", "../../../tmp/pwned", "a b",
                'x"; rm -rf ~; #', "", "n" * 200, "foo$(id)", "a|b"]:
        try:
            JobSpec.model_validate({"name": bad, "executable": "x"})
        except Exception:
            continue
        raise AssertionError(f"job name accepted but should be rejected: {bad!r}")
    for ok in ["agent-job", "hub-smoke", "train_vit.v2", "probe360"]:
        JobSpec.model_validate({"name": ok, "executable": "x"})

    # Same primitive, every other field rendered into a directive line.
    for label, extra in [
        ("queue_name", {"attributes": {"queue_name": "gpu\nrm -rf /"}}),
        ("account", {"attributes": {"account": "a\n#SBATCH --uid=0"}}),
        ("reservation_id", {"attributes": {"reservation_id": "r\nevil"}}),
        ("custom key", {"attributes": {"custom_attributes": {"k\nevil": "v"}}}),
        ("custom value", {"attributes": {"custom_attributes": {"k": "v\nevil"}}}),
        ("directory", {"directory": "/tmp\nevil"}),
        ("stdout_path", {"stdout_path": "o\nevil"}),
    ]:
        spec = {"name": "t", "executable": "x", **extra}
        try:
            JobSpec.model_validate(spec)
        except Exception:
            continue
        raise AssertionError(f"control character accepted in {label}")

    # duration is both a directive-line field on every scheduler and a
    # login-node shell argument for Fugaku's pjalter update path.
    for bad in ["00:01:00\nrm -rf /", '00:01:00"; touch /tmp/pwned; #',
                "not-a-duration", "00:60:00", True, 0, -1]:
        try:
            JobSpec.model_validate({
                "name": "t", "executable": "x",
                "attributes": {"duration": bad},
            })
        except Exception:
            continue
        raise AssertionError(f"unsafe/invalid duration accepted: {bad!r}")
