from hpc_agent_core.models import Job, JobSpec
from unittest.mock import patch

LABEL = 'Bridge shape, parsers, and machine limits are enforced'
FACILITIES = ('irene',)


def irene_bridge_shape_and_limits_are_enforced(slug, fac, backend):
    """Exercise Bridge rendering and parsers without a TGCC connection."""
    if slug != "irene":
        return

    spec = JobSpec(
        name="bridge-shape",
        executable="./solver",
        arguments=["input with spaces"],
        environment={"OMP_NUM_THREADS": "2"},
        resources={
            "node_count": 2, "process_count": 8,
            "cpu_cores_per_process": 2, "exclusive_node_use": True,
        },
        attributes={
            "queue_name": "rome", "account": "gen-test", "duration": 600,
            "custom_attributes": {"filesystems": "scratch,work", "qos": "test"},
        },
    )
    script = backend.render_script(spec)
    assert "#MSUB -q rome" in script
    assert "#MSUB -A gen-test" in script
    assert "#MSUB -m scratch,work" in script
    assert "#MSUB -n 8" in script
    assert "#MSUB -c 2" in script
    assert "#MSUB -N 2" in script
    assert "#MSUB -x" in script
    assert "#MSUB -T 600" in script
    assert "#MSUB -Q test" in script
    assert "cd ${BRIDGE_MSUB_PWD}" in script
    assert "ccc_mprun ./solver 'input with spaces'" in script

    gpu = JobSpec(
        executable="gpu-app", resources={"process_count": 4, "gpus": 4},
        attributes={"queue_name": "v100", "account": "gen-test",
                    "custom_attributes": {"filesystems": "scratch"}},
    )
    gpu_script = backend.render_script(gpu)
    assert "#MSUB -c 10" in gpu_script
    assert "ccc_mprun gpu-app" in gpu_script

    container = JobSpec(
        executable="python", arguments=["run.py"],
        container={"image": "registry/image:tag",
                   "volume_mounts": [{"source": "/work/a", "target": "/data"}]},
        attributes={"queue_name": "rome", "account": "gen-test",
                    "custom_attributes": {"filesystems": "work"}},
    )
    assert "ccc_mprun -C registry/image:tag -E" in backend.render_script(container)

    invalid_specs = [
        ({}, {"queue_name": "skylake", "account": "gen-test"}),
        ({"gpus": 1}, {"queue_name": "rome", "account": "gen-test"}),
        ({"node_count": 2}, {"queue_name": "xlarge", "account": "gen-test"}),
        ({"memory": 1024}, {"queue_name": "rome", "account": "gen-test"}),
        ({"process_count": 129}, {"queue_name": "rome", "account": "gen-test"}),
        ({"process_count": 2, "gpus": 1}, {"queue_name": "v100", "account": "gen-test"}),
    ]
    for resources, attributes in invalid_specs:
        attributes.setdefault("custom_attributes", {"filesystems": "scratch,work"})
        bad = JobSpec(executable="true", resources=resources, attributes=attributes)
        try:
            backend.validate_spec(bad)
        except ValueError:
            continue
        raise AssertionError(
            f"Irene accepted invalid spec: resources={resources}, attributes={attributes}"
        )

    from facilities.irene.compute import (
        _parse_ccc_mpp, _parse_ccc_msub_job_id, _parse_compuse_projects,
        _parse_macct,
    )
    projects = _parse_compuse_projects(
        "gen1@rome OK normal\ngen1@v100 BONUS\ngen1@rome duplicate"
    )
    assert [(p["account"], p["partition"]) for p in projects] == [
        ("gen1", "rome"), ("gen1", "v100")]
    assert _parse_ccc_msub_job_id("Submitted batch job 123456") == "123456"
    jobs = _parse_ccc_mpp(
        "user gen1 123456 8 rome batch RUN 00:10:00 now x x bridge-job node01"
    )
    assert jobs[0].status.state.value == "active"
    assert _parse_macct(
        "123456", "Date: 19/08/2026 12:00:00\nStep COMPLETED"
    ).status.state.value == "completed"

    with patch(
        "facilities.irene.compute.run_command",
        return_value="gen1@rome OK normal\ngen1@v100 BONUS",
    ) as command:
        assert backend.get_projects() == [{
            "account": "gen1", "partitions": ["rome", "v100"],
            "status": [
                {"partition": "rome", "value": "OK normal"},
                {"partition": "v100", "value": "BONUS"},
            ],
        }]
    assert command.call_args.args == ("irene", "ccc_compuse")

    with patch(
        "facilities.irene.compute.run_command",
        side_effect=RuntimeError("Irene connection failed"),
    ):
        try:
            backend.get_projects()
        except RuntimeError as exc:
            assert "Irene connection failed" in str(exc)
        else:
            raise AssertionError(
                "Irene returned an empty project list after its query failed"
            )

    with patch(
        "facilities.irene.compute.run_command",
        side_effect=["gen-test@rome OK", "Submitted batch job 123456"],
    ) as command, patch(
        "facilities.irene.compute.write_remote_file",
        return_value="agent/jobs/bridge-shape.sh",
    ) as write:
        submitted = backend.submit(spec)
    assert submitted["job_id"] == "123456"
    assert write.call_args.args[0] == "irene"
    assert command.call_args_list[0].args == ("irene", "ccc_compuse")
    assert command.call_args_list[1].args == (
        "irene", "ccc_msub agent/jobs/bridge-shape.sh",
    )

    update_spec = JobSpec(executable="true", attributes={"duration": 300})
    updated = Job(id="123456", status={"state": "active"})
    with patch("facilities.irene.compute.run_command") as command, \
            patch.object(backend, "get_statuses", return_value=[updated]):
        backend.update("123456", update_spec)
    assert command.call_args.args == (
        "irene", "ccc_malter -T 300 123456",
    )
