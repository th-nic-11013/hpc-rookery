from hpc_agent_core.models import JobSpec

LABEL = 'deterministic machine resource limits are enforced'
FACILITIES = ('rikyu',)


def machine_resource_limits_are_enforced(slug, fac, backend):
    """Exercise deterministic limits encoded by the facility hook.

    Only RIKYU currently has a complete job-resource table precise
    enough for these checks; facilities whose limits vary by project or
    live partition intentionally do not guess here.
    """
    if slug != "rikyu":
        return
    invalid_specs = [
        {"gpus": 5},
        {"gpus": 8, "node_count": 1},
        {"gpus": 1, "cpu_cores_per_process": 37},
        {"gpus": 1, "memory": 401 * 1024**3},
    ]
    for resources in invalid_specs:
        spec = JobSpec(
            executable="true", resources=resources,
            attributes={"queue_name": "gpu"},
        )
        try:
            backend.validate_spec(spec)
        except ValueError:
            continue
        raise AssertionError(f"RIKYU accepted invalid resources: {resources}")

    wrong_queue = JobSpec(
        executable="true", attributes={"queue_name": "not-gpu"},
    )
    try:
        backend.validate_spec(wrong_queue)
    except ValueError:
        return
    raise AssertionError("RIKYU accepted a nonexistent partition")
