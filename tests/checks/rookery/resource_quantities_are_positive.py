from hpc_agent_core.models import JobSpec

LABEL = 'resource quantities are positive'


def resource_quantities_are_positive():
    invalid = [
        {"node_count": 0}, {"node_count": -1},
        {"process_count": 0}, {"process_count": -1},
        {"processes_per_node": 0}, {"cpu_cores_per_process": 0},
        {"gpu_cores_per_process": 0}, {"gpus": -1}, {"memory": 0},
    ]
    invalid += [
        {field: True} for field in (
            "node_count", "process_count", "processes_per_node",
            "cpu_cores_per_process", "gpu_cores_per_process", "gpus", "memory",
        )
    ]
    for resources in invalid:
        try:
            JobSpec.model_validate({"executable": "x", "resources": resources})
        except Exception:
            continue
        raise AssertionError(f"invalid resource quantity accepted: {resources!r}")
