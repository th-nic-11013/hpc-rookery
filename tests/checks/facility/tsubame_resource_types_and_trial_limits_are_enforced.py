import os
from hpc_agent_core.models import Job, JobSpec
from unittest.mock import patch
from checks._common import no_user_config

LABEL = 'resource-type Grid Engine dialect and trial limits are enforced'
FACILITIES = ('tsubame',)


def tsubame_resource_types_and_trial_limits_are_enforced(slug, fac, backend):
    """Exercise TSUBAME's non-generic Grid Engine resource dialect."""
    if slug != "tsubame":
        return

    with no_user_config(slug), patch.dict(os.environ, {"TSUBAME_GROUP": ""}):
        trial = JobSpec(
            name="ge-shape",
            executable="hostname",
            container={"image": "$HOME/test image.sif"},
            resources={"node_count": 2, "processes_per_node": 4,
                       "cpu_cores_per_process": 2},
            attributes={
                "duration": 180,
                "account": "",
                "custom_attributes": {
                    "resource_type": "gpu_1", "priority": "-5",
                    "array": "1-4:2", "gpu_compute_mode": "1",
                },
            },
        )
        backend.apply_defaults(trial)
        script = backend.render_script(trial)
    assert "#$ -l gpu_1=2" in script
    assert "#$ -l h_rt=00:03:00" in script
    assert "#$ -p -5" in script
    assert "#$ -t 1-4:2" in script
    assert "#$ -v GPU_COMPUTE_MODE=1" in script
    assert "export OMP_NUM_THREADS=2" in script
    assert "apptainer exec -B /gs -B /apps -B /home --nv" in script
    assert '"$HOME"/' in script

    cpu_container = JobSpec(
        executable="true", container={"image": "image.sif"},
        attributes={"account": "paid", "duration": 3600,
                    "custom_attributes": {"resource_type": "cpu_40"}},
    )
    backend.apply_defaults(cpu_container)
    cpu_script = backend.render_script(cpu_container)
    assert "#$ -l cpu_40=1" in cpu_script
    assert "--nv" not in cpu_script

    invalid_specs = [
        ({"node_count": 3}, {"account": "", "duration": 180}),
        ({"node_count": 1}, {"account": "", "duration": 181}),
        ({}, {"account": "", "duration": 180,
              "custom_attributes": {"priority": "-4"}}),
        ({}, {"account": "paid", "duration": "24:00:01"}),
        ({}, {"account": "paid", "custom_attributes": {"resource_type": "gpu_2"}}),
        ({"gpus": 1}, {"account": "paid"}),
        ({"memory": 1024}, {"account": "paid"}),
        ({"processes_per_node": 9}, {"account": "paid",
             "custom_attributes": {"resource_type": "gpu_1"}}),
        ({}, {"account": "paid",
              "custom_attributes": {"resource_type": "gpu_h",
                                    "gpu_compute_mode": "1"}}),
        ({}, {"account": "paid", "queue_name": "all.q"}),
        ({}, {"account": "", "queue_name": "prior"}),
        ({}, {"account": "paid", "custom_attributes": {"unknown": "x"}}),
    ]
    for resources, attributes in invalid_specs:
        bad = JobSpec(executable="true", resources=resources, attributes=attributes)
        backend.apply_defaults(bad)
        try:
            backend.validate_spec(bad)
        except ValueError:
            continue
        raise AssertionError(
            f"TSUBAME accepted invalid spec: resources={resources}, "
            f"attributes={attributes}"
        )

    assert backend._parse_qstat_line(
        "123 0.555 job user hRwq 08/19/2026 12:34:56 1"
    ).status.state.value == "held"
    assert backend._parse_qstat_line(
        "124 0.555 job user r 08/19/2026 12:34:56 all.q@node 8"
    ).status.state.value == "active"
    from facilities.tsubame.compute import _final_state
    assert _final_state("0", "0").value == "completed"
    assert _final_state("0", "2").value == "failed"
    assert _final_state(None, None).value == "unknown"

    update_spec = JobSpec(
        name="renamed", executable="true",
        attributes={"duration": 600,
                    "custom_attributes": {"priority": "-4", "hold_jid": "41"}},
    )
    updated = Job(
        id="42", status={"state": "queued", "message": None},
    )
    with patch("facilities.tsubame.compute.run_command") as command, \
            patch.object(backend, "get_statuses", return_value=[updated]):
        backend.update("42", update_spec)
    rendered_command = command.call_args.args[1]
    assert rendered_command == (
        "qalter -N renamed -l h_rt=00:10:00 -p -4 -hold_jid 41 42"
    )
