import pathlib
import tempfile
from hpc_agent_core.models import Job, JobSpec
from unittest.mock import patch
from checks._common import bare_spec

LABEL = 'dual-scheduler rendering, validation, and routing are enforced'
FACILITIES = ('cell2026',)


def cell2026_dual_scheduler_routing_is_enforced(slug, fac, backend):
    """Exercise both schedulers and the local routing index offline."""
    if slug != "cell2026":
        return

    # AGE may be outside PATH. Doctor must check the same configured
    # Grid Engine binaries that the runtime backend invokes.
    from facilities.cell2026.compute import Cell2026Backend
    configured = Cell2026Backend("cell2026", ge_bin_prefix="/opt/age/bin")
    with patch("hpc_agent_core.doctor.check_commands_on_path",
               return_value=True) as check_commands:
        assert configured.check_scheduler()
    checked = check_commands.call_args.args[1]
    assert "/opt/age/bin/qsub" in checked
    assert "/opt/age/bin/qstat" in checked
    assert "sbatch" in checked

    try:
        backend.get_projects()
    except NotImplementedError as exc:
        assert "neither Grid Engine nor Slurm exposes per-project accounting" in str(exc)
    else:
        raise AssertionError(
            "cell2026 returned an empty project list despite lacking project accounting"
        )

    # A successful resource list must cover both schedulers.  An empty
    # list after failed SSH/configuration probes looks like valid empty
    # occupancy to an MCP client, which is worse than a clear error.
    with patch.object(
        backend._slurm, "get_live_resources", return_value=[],
    ), patch(
        "facilities.cell2026.compute.run_command", return_value="",
    ):
        assert backend.get_live_resources() == []

    with patch.object(
        backend._slurm, "get_live_resources",
        side_effect=RuntimeError("Slurm connection failed"),
    ), patch(
        "facilities.cell2026.compute.run_command",
        side_effect=RuntimeError("Grid Engine connection failed"),
    ):
        try:
            backend.get_live_resources()
        except RuntimeError as exc:
            message = str(exc)
            assert "incomplete" in message
            assert "Slurm, Grid Engine" in message
            assert "Slurm connection failed" in message
        else:
            raise AssertionError(
                "cell2026 returned a resource list after both scheduler probes failed"
            )

    with patch.object(
        backend._slurm, "get_live_resources",
        return_value=[{"partition": "all", "nodes_total": 2}],
    ), patch(
        "facilities.cell2026.compute.run_command",
        side_effect=RuntimeError("Grid Engine connection failed"),
    ):
        try:
            backend.get_live_resources()
        except RuntimeError as exc:
            message = str(exc)
            assert "incomplete" in message
            assert "Grid Engine" in message
            assert "Grid Engine connection failed" in message
        else:
            raise AssertionError(
                "cell2026 returned partial scheduler occupancy as a complete list"
            )

    default = bare_spec()
    backend.apply_defaults(default)
    assert default.attributes.queue_name == "all.q"
    ge_script = backend.render_script(JobSpec(
        name="cell-ge", executable="echo ok",
        resources={"gpus": 1, "cpu_cores_per_process": 4},
        attributes={"queue_name": "all.q", "duration": 300},
    ))
    assert "#$ -q all.q" in ge_script
    assert "#$ -l gpu=1" in ge_script
    assert "#$ -pe smp 4" in ge_script
    assert "SGE_HGR_gpu" in ge_script
    assert "CUDA_VISIBLE_DEVICES" in ge_script
    assert "export OMP_NUM_THREADS=4" in ge_script
    assert "#SBATCH" not in ge_script

    ge_host = backend.render_script(JobSpec(
        executable="true", attributes={"queue_name": "helix"},
    ))
    assert "#$ -q all.q" in ge_host
    assert "#$ -l hostname=helix" in ge_host

    slurm_host = backend.render_script(JobSpec(
        name="cell-slurm", executable="echo ok",
        resources={"cpu_cores_per_process": 2, "memory": None},
        attributes={"queue_name": "beta", "duration": 300},
    ))
    assert "#SBATCH --partition=all" in slurm_host
    assert "#SBATCH --nodelist=beta" in slurm_host
    assert "#SBATCH --cpus-per-task=2" in slurm_host
    assert "--mem" not in slurm_host
    assert "--gres" not in slurm_host
    assert "#$ " not in slurm_host

    slurm_gpu = backend.render_script(JobSpec(
        executable="true", resources={"gpus": 1},
        attributes={"queue_name": "serine"},
        container={"image": "$HOME/test.sif"},
    ))
    assert "#SBATCH --nodelist=serine" in slurm_gpu
    assert "--gres" not in slurm_gpu
    assert "singularity exec --nv" in slurm_gpu

    ge_submit_spec = JobSpec(
        name="submit-ge", executable="true", resources={"gpus": 1},
        attributes={"queue_name": "helix"},
    )
    with patch(
        "hpc_agent_core.compute.gridengine.write_remote_file",
        return_value="agent/jobs/submit-ge.sh",
    ) as ge_write, patch(
        "hpc_agent_core.compute.gridengine.run_command", return_value="301\n",
    ) as ge_command, patch(
        "facilities.cell2026.registry.record",
    ) as record:
        assert backend.submit(ge_submit_spec)["job_id"] == "301"
    assert ge_write.call_args.args[0] == "cell2026"
    assert ge_command.call_args.args[0] == "cell2026"
    assert record.call_args.args[:3] == ("301", "gridengine", "helix")

    slurm_submit_spec = JobSpec(
        name="submit-slurm", executable="true",
        attributes={"queue_name": "beta"},
    )
    with patch(
        "hpc_agent_core.compute.slurm.write_remote_file",
        return_value="agent/jobs/submit-slurm.sh",
    ) as slurm_write, patch(
        "hpc_agent_core.compute.slurm.run_command", return_value="302\n",
    ) as slurm_command, patch(
        "facilities.cell2026.registry.record",
    ) as record:
        assert backend.submit(slurm_submit_spec)["job_id"] == "302"
    assert slurm_write.call_args.args[0] == "cell2026"
    assert slurm_command.call_args.args == (
        "cell2026", "sbatch --parsable agent/jobs/submit-slurm.sh",
    )
    assert record.call_args.args[:3] == ("302", "slurm", "all")

    explicit_slurm = JobSpec(
        executable="true", attributes={"scheduler": "slurm"},
    )
    backend.apply_defaults(explicit_slurm)
    assert explicit_slurm.attributes.queue_name == "all"

    invalid_specs = [
        ({"gpus": 3}, {"queue_name": "all.q"}, {}),
        ({"process_count": 33}, {"queue_name": "all.q"}, {}),
        ({"node_count": 2}, {"queue_name": "helix"}, {}),
        ({"memory": 1024}, {"queue_name": "all.q"}, {}),
        ({"gpus": 1}, {"queue_name": "all.q"}, {"CUDA_VISIBLE_DEVICES": "0"}),
        ({}, {"queue_name": "all.q", "account": "project"}, {}),
        ({"node_count": 2}, {"queue_name": "beta"}, {}),
        ({"process_count": 9}, {"queue_name": "all"}, {}),
        ({"gpus": 1}, {"queue_name": "serine"}, {}),
        ({"gpus": 2}, {"queue_name": "all"}, {}),
        ({}, {"queue_name": "all", "custom_attributes": {"constraint": "gpu"}}, {}),
        ({}, {"queue_name": "not-a-queue"}, {}),
        ({}, {"queue_name": "all.q", "scheduler": "slurm"}, {}),
    ]
    for resources, attributes, environment in invalid_specs:
        bad = JobSpec(
            executable="true", resources=resources,
            attributes=attributes, environment=environment,
        )
        try:
            backend.validate_spec(bad)
        except ValueError:
            continue
        raise AssertionError(
            f"cell2026 accepted invalid spec: resources={resources}, "
            f"attributes={attributes}, environment={environment}"
        )

    from facilities.cell2026 import registry as cell_registry
    with tempfile.TemporaryDirectory() as temporary:
        with patch.object(cell_registry, "_REGISTRY_DIR", pathlib.Path(temporary)):
            cell_registry.record("101", "slurm", "all", "agent/jobs/a.sh")
            cell_registry.record("202", "gridengine", "all.q", "agent/jobs/b.sh")
            assert cell_registry.lookup("101") == "slurm"
            assert cell_registry.lookup("202") == "gridengine"
            assert {row["job_id"] for row in cell_registry.recent()} == {"101", "202"}
            assert cell_registry.lookup("../escape") is None
            cell_registry.record("101", "gridengine", "all.q", "agent/jobs/c.sh")
            assert cell_registry.lookup("101") is None, (
                "overlapping IDs registered to both schedulers must stay ambiguous"
            )

    slurm_job = Job(id="101", status={"state": "active",
                                       "meta_data": {"native_state": "RUNNING"}})
    ge_job = Job(id="202", status={"state": "completed",
                                   "meta_data": {"native_state": "qacct"}})
    with patch(
        "facilities.cell2026.registry.lookup",
        side_effect=lambda job_id: {"101": "slurm", "202": "gridengine"}.get(job_id),
    ), patch.object(
        backend._slurm, "get_statuses", return_value=[slurm_job],
    ), patch.object(
        backend._gridengine, "get_statuses", return_value=[ge_job],
    ):
        routed = backend.get_statuses(["202", "101"])
    assert [job.id for job in routed] == ["202", "101"]
    assert all(job.status.meta_data["scheduler_source"] == "registry"
               for job in routed)

    # run_command is stubbed so both liveness probes succeed: this case
    # is about a genuinely ambiguous id, not an unreachable scheduler.
    with patch("facilities.cell2026.registry.lookup", return_value=None), \
            patch("hpc_agent_core.middleware.run_command", return_value=""), \
            patch.object(backend._slurm, "get_statuses", return_value=[slurm_job]), \
            patch.object(backend._gridengine, "get_statuses", return_value=[ge_job]), \
            patch.object(backend._slurm, "cancel") as slurm_cancel, \
            patch.object(backend._gridengine, "cancel") as ge_cancel:
        try:
            backend.cancel("77")
        except ValueError as exc:
            assert "exists in both" in str(exc), exc
        else:
            raise AssertionError("cell2026 canceled an ambiguous cross-scheduler ID")
    slurm_cancel.assert_not_called()
    ge_cancel.assert_not_called()

    # An unregistered id with only ONE scheduler answering must not be
    # cancelled on that scheduler: _is_not_found() reports the same shape
    # for "absent" and "the query failed", and this facility's Slurm runs
    # has_accounting=False, which swallows both squeue and scontrol
    # errors -- so a dead link looks exactly like a clean miss. Ids can
    # collide across the two schedulers, so guessing can kill a different
    # job.
    missing = Job(id="77", status={"state": "unknown"})
    with patch("facilities.cell2026.registry.lookup", return_value=None), \
            patch("hpc_agent_core.middleware.run_command",
                  side_effect=RuntimeError("ssh: connect to host: timed out")), \
            patch.object(backend._slurm, "get_statuses", return_value=[slurm_job]), \
            patch.object(backend._gridengine, "get_statuses", return_value=[missing]), \
            patch.object(backend._slurm, "cancel") as slurm_cancel, \
            patch.object(backend._gridengine, "cancel") as ge_cancel:
        try:
            backend.cancel("77")
        except ValueError as exc:
            assert "could not be completed" in str(exc), exc
        else:
            raise AssertionError(
                "cell2026 cancelled an unregistered id while a scheduler "
                "lookup was inconclusive"
            )
    slurm_cancel.assert_not_called()
    ge_cancel.assert_not_called()

    # With both schedulers demonstrably answering and exactly one hit,
    # the fallback cancel still works.
    with patch("facilities.cell2026.registry.lookup", return_value=None), \
            patch("hpc_agent_core.middleware.run_command", return_value=""), \
            patch.object(backend._slurm, "get_statuses", return_value=[slurm_job]), \
            patch.object(backend._gridengine, "get_statuses", return_value=[missing]), \
            patch.object(backend._slurm, "cancel",
                         return_value=Job(id="101", status={"state": "canceled"})) as ok_cancel, \
            patch.object(backend._gridengine, "cancel") as ge_cancel2:
        cancelled = backend.cancel("101")
    ok_cancel.assert_called_once()
    ge_cancel2.assert_not_called()
    assert cancelled.status.meta_data["cancel_source"] == "slurm_fallback"

    update_spec = JobSpec(executable="true", attributes={"duration": 600})
    with patch("facilities.cell2026.registry.lookup", return_value="slurm"), \
            patch.object(backend._slurm, "update", return_value=slurm_job) as update:
        backend.update("101", update_spec)
    assert update.call_args.args == ("101", update_spec)
