import json
from hpc_agent_core.models import Job, JobSpec
from unittest.mock import patch
from checks._common import no_user_config

LABEL = 'ALCF PBS shape, limits, parsers, and sbank accounting are enforced'
FACILITIES = ('crux',)


def crux_pbs_shape_and_limits_are_enforced(slug, fac, backend):
    """Keep the offline Crux port honest while no live access exists.

    Every fixture here is synthetic — invented rows in the shapes the
    ALCF/PBS documentation describes, never copied example output —
    and the "at request of scheduler" comment is the machine's own
    literal message text the parser must recognize.
    """
    if slug != "crux":
        return

    from facilities.crux.compute import _duration_seconds, _walltime

    def spec_with(**overrides):
        resources = overrides.pop("resources", {"node_count": 1})
        attributes = {"queue_name": "debug", "account": "Catalyst",
                      "duration": 600}
        attributes.update(overrides.pop("attributes", {}))
        return JobSpec(executable="hostname", resources=resources,
                       attributes=attributes)

    # Rendered shape: the documented ALCF directive block, whole-node
    # select, mandatory walltime/filesystems, forced -k doe, and the
    # job body starting from PBS_O_WORKDIR.
    with no_user_config(slug):
        shape = spec_with(resources={"node_count": 2})
        backend.apply_defaults(shape)
        script = backend.render_script(shape)
    assert "#!/bin/bash -l" in script
    assert "#PBS -q debug" in script
    assert "#PBS -A Catalyst" in script
    assert "#PBS -l select=2:system=crux" in script
    assert "#PBS -l place=scatter" in script
    assert "#PBS -l walltime=00:10:00" in script
    assert "#PBS -l filesystems=home:eagle" in script
    assert "#PBS -k doe" in script
    assert "#PBS -r y" not in script
    assert 'cd "$PBS_O_WORKDIR"' in script
    assert "hostname" in script
    # No mpiprocs/ompthreads: ALCF allocates whole nodes and rank
    # placement belongs to mpiexec, never to the select statement.
    assert "mpiprocs" not in script
    assert "ompthreads" not in script

    # Defaults: production queue filled when the caller is silent, a
    # reservation suppresses it, and filesystems default from facts.
    with no_user_config(slug):
        default = JobSpec(executable="true",
                          attributes={"account": "Catalyst", "duration": 600})
        backend.apply_defaults(default)
        assert default.attributes.queue_name == "workq-route"
        assert "#PBS -l filesystems=home:eagle" in backend.render_script(default)

        reserved = JobSpec(executable="true", attributes={
            "account": "Catalyst", "duration": 600,
            "reservation_id": "A123456"})
        backend.apply_defaults(reserved)
        assert reserved.attributes.queue_name == ""
        assert "#PBS -q A123456" in backend.render_script(reserved)

        preemptable = spec_with(attributes={"queue_name": "preemptable",
                                            "duration": "48:00:00"})
        backend.apply_defaults(preemptable)
        assert "#PBS -r y" in backend.render_script(preemptable)

        fs_only = spec_with(attributes={
            "custom_attributes": {"filesystems": "eagle"}})
        backend.apply_defaults(fs_only)
        assert "#PBS -l filesystems=eagle" in backend.render_script(fs_only)

    # A directive line is one line of options, so a value with a space
    # injects a second option without needing a newline — every
    # rendered field is checked against its real syntax instead.
    injections = [
        {"queue_name": "debug -l place=excl", "account": "Catalyst"},
        {"queue_name": "workq-route", "account": "Catalyst -l place=excl"},
        {"queue_name": "workq-route", "account": "grp; qdel 1"},
        {"queue_name": "no-such-queue", "account": "Catalyst"},
        {"queue_name": "workq-route", "account": "Catalyst",
         "reservation_id": "A123456"},                          # two destinations
        {"queue_name": "workq-route", "account": "Catalyst",
         "reservation_id": "A123456 -x"},                      # injectable reservation
        {"queue_name": "workq-route", "account": "Catalyst",
         "custom_attributes": {"filesystems": "home:eagle -l place=excl"}},
        {"queue_name": "workq-route", "account": "Catalyst",
         "custom_attributes": {"unknown": "value"}},           # dropped attribute
    ]
    for attrs in injections:
        bad = JobSpec(executable="true", resources={"node_count": 1},
                      attributes={"duration": 600, **attrs})
        try:
            backend.apply_defaults(bad)
            backend.validate_spec(bad)
        except ValueError:
            continue
        raise AssertionError(
            f"crux accepted an injectable or unmappable value: {attrs}")

    # Offline queue limits exist because of the routing-queue trap: a
    # job outside its queue's range is accepted and then dropped with
    # no error at all (see the compute module's docstring).
    limit_violations = [
        ("debug", {"node_count": 5}, 600),
        ("debug", {"node_count": 1}, "02:00:01"),
        ("debug", {"node_count": 1}, 240),          # below the 5-minute floor
        ("workq-route", {"node_count": 177}, 600),
        ("workq-route", {"node_count": 1}, "25:00:00"),
        ("preemptable", {"node_count": 11}, 600),
        ("preemptable", {"node_count": 1}, "73:00:00"),
        ("demand", {"node_count": 1}, "01:01:00"),
    ]
    for queue, resources, duration in limit_violations:
        bad = JobSpec(executable="true", resources=resources,
                      attributes={"queue_name": queue, "account": "Catalyst",
                                  "duration": duration})
        try:
            backend.validate_spec(bad)
        except ValueError:
            continue
        raise AssertionError(
            f"crux accepted an out-of-range request for {queue}: "
            f"resources={resources}, duration={duration}")

    # Machine facts Crux simply cannot satisfy.
    impossible = [
        {"gpus": 1},                                      # CPU-only system
        {"gpu_cores_per_process": 1},
        {"memory": 300 * 1024**3},                        # > 256 GiB node
        {"processes_per_node": 300},                      # > 256 hw threads
    ]
    for resources in impossible:
        bad = JobSpec(executable="true", resources=resources,
                      attributes={"queue_name": "workq-route",
                                  "account": "Catalyst", "duration": 600})
        try:
            backend.validate_spec(bad)
        except ValueError:
            continue
        raise AssertionError(
            f"crux accepted an unsatisfiable resource request: {resources}")

    bad = spec_with()
    bad.container = {"image": "$HOME/app.sif"}
    try:
        backend.validate_spec(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("crux accepted a container spec (no Apptainer yet)")

    bad = spec_with()
    bad.stdin_path = "/home/u/input.dat"
    try:
        backend.validate_spec(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("crux accepted an unmapped stdin_path")

    # Output/error paths: quoted so a space can't split the directive,
    # and $HOME-anchored because qsub runs from the jobs directory.
    spaced = spec_with()
    spaced.stdout_path = "results/run dir/job.out"
    spaced.stderr_path = "results/run dir/job.err"
    spaced_script = backend.render_script(spaced)
    assert "#PBS -o '../results/run dir/job.out'" in spaced_script, spaced_script
    assert "#PBS -e '../results/run dir/job.err'" in spaced_script, spaced_script
    absolute = spec_with()
    absolute.stdout_path = "/eagle/projects/Catalyst/logs/job.out"
    assert ("#PBS -o /eagle/projects/Catalyst/logs/job.out"
            in backend.render_script(absolute))
    # Explicit working directories are anchored the same way.
    workdir = spec_with()
    workdir.directory = "agent/work/run1"
    assert 'cd "$HOME"/agent/work/run1' in backend.render_script(workdir)

    # Duration helpers: the model accepts D-HH:MM:SS, PBS wants plain
    # HH:MM:SS with hours free to exceed 24.
    assert _duration_seconds("2-00:00:00") == 172800
    assert _duration_seconds("0:10:00") == 600
    assert _duration_seconds(300) == 300
    assert _walltime("2-00:00:00") == "48:00:00"
    assert _walltime(600) == "00:10:00"

    # Job record mapping from qstat -F JSON fields.
    finished = backend._job("7001.crux-pbs-0001.head.cm.crux.alcf.anl.gov", {
        "Job_Name": "demo-run", "job_state": "F", "queue": "workq",
        "Account_Name": "Catalyst", "Exit_status": 0,
    })
    assert finished.status.state.value == "completed"
    assert finished.status.exit_code == 0
    failed = backend._job("7002.crux-pbs-0001.head.cm.crux.alcf.anl.gov", {
        "job_state": "F", "Exit_status": 2, "comment": "not a preemption",
    })
    assert failed.status.state.value == "failed"
    assert failed.status.exit_code == 2
    preempted = backend._job("7003.crux-pbs-0001.head.cm.crux.alcf.anl.gov", {
        "job_state": "F", "Exit_status": 271,
        "comment": "Job to be deleted at request of Scheduler",
    })
    assert preempted.status.state.value == "failed"   # scheduler killed it, not the user
    assert "request of Scheduler" in preempted.status.message
    running = backend._job("7004.crux-pbs-0001.head.cm.crux.alcf.anl.gov", {
        "job_state": "R", "queue": "workq",
        "Variable_List": ("PBS_O_HOME=/home/u,PBS_O_QUEUE=workq-route,"
                          "PBS_O_WORKDIR=/home/u/agent/jobs"),
        "resources_used": {"walltime": "00:03:21"},
    })
    assert running.status.state.value == "active"
    assert running.status.meta_data["workdir"] == "/home/u/agent/jobs"
    assert running.status.meta_data["walltime_used"] == "00:03:21"
    assert backend._pbs_workdir("PBS_O_HOME=/home/u") == ""
    # Finished jobs carry no Variable_List at all (confirmed live); the
    # submission directory must come from Output_Path instead, and the
    # output/error destinations are surfaced as usable paths.
    historical = backend._job("7006.crux-pbs-0001.head.cm.crux.alcf.anl.gov", {
        "job_state": "F", "Exit_status": 0,
        "Output_Path": ("crux-uan-0001.head.cm.crux.alcf.anl.gov:"
                        "/home/u/agent/jobs/demo-run.o7006"),
        "Error_Path": ("crux-uan-0001.head.cm.crux.alcf.anl.gov:"
                       "/home/u/agent/jobs/demo-run.e7006"),
        "comment": "Job run at Mon Nov 02 at 11:29 on a vnode and finished",
    })
    assert historical.status.state.value == "completed"
    assert historical.status.meta_data["workdir"] == "/home/u/agent/jobs"
    assert (historical.status.meta_data["output_path"]
            == "/home/u/agent/jobs/demo-run.o7006")
    assert (historical.status.meta_data["error_path"]
            == "/home/u/agent/jobs/demo-run.e7006")
    assert backend._strip_host("crux-host:/home/u/x") == "/home/u/x"
    assert backend._strip_host("/home/u/x") == "/home/u/x"

    # JSON record parsing, including seq-only id matching.
    qstat_output = '''{
          "timestamp": 1790000000,
          "pbs_version": "2022.1",
          "pbs_server": "crux-pbs-0001.head.cm.crux.alcf.anl.gov",
          "Jobs": {
            "7004.crux-pbs-0001.head.cm.crux.alcf.anl.gov": {
              "Job_Name": "demo-run", "job_state": "R", "queue": "workq",
              "Variable_List": "PBS_O_WORKDIR=/home/u/agent/jobs"
            }
          }
        }'''
    records = backend._parse_qstat_json(qstat_output)
    assert list(records) == ["7004.crux-pbs-0001.head.cm.crux.alcf.anl.gov"]
    # A finished job's record keeps Output_Path (history drops
    # Variable_List) so workdir survives into history.
    historical_records = backend._parse_qstat_json(json.dumps({
        "Jobs": {"7006.crux-pbs-0001.head.cm.crux.alcf.anl.gov": {
            "job_state": "F", "Exit_status": 0,
            "Output_Path": ("crux-uan-0001.head.cm.crux.alcf.anl.gov:"
                            "/home/u/agent/jobs/demo-run.o7006"),
        }}
    }))
    assert backend._job(
        "7006.crux-pbs-0001.head.cm.crux.alcf.anl.gov",
        historical_records["7006.crux-pbs-0001.head.cm.crux.alcf.anl.gov"],
    ).status.meta_data["workdir"] == "/home/u/agent/jobs"

    with patch("facilities.crux.compute.run_command",
               return_value=qstat_output) as command:
        found = backend.get_statuses(["7004"])[0]
    assert command.call_args.args[1] == "qstat -fx -F JSON 7004"
    assert found.id.endswith("crux.alcf.anl.gov")
    assert found.status.state.value == "active"

    # A missing id must explain the routing-queue trap, not just vanish.
    with patch("facilities.crux.compute.run_command",
               side_effect=RuntimeError("qstat: Unknown Job Id 9999")):
        missing = backend.get_statuses(["9999"])[0]
    assert missing.status.state.value == "unknown"
    assert "node count and walltime" in missing.status.message

    # sbank parsing: ALCF's live layout adds a Suballocation column to
    # sbank's documented default, and both are handled by shape (the
    # start/end date pair), not by fixed position. Synthetic rows with
    # the real layouts; multi-word project names; a negative balance;
    # and a Totals section that must not become data.
    sbank_alcf_layout = '''
         Allocation  Suballocation  Start       End         Resource  Project      Jobs  Charged  Available Balance
         ----------  -------------  ----------  ----------  --------  ---------  ----  -------  -----------------
         7001        7011           2026-01-01  2026-12-31  crux      Catalyst      1,234  12,345.6  87,654.3
         7002        7012           2026-01-01  2026-12-31  crux      My Project      987  -1,234.5   1,234.5
         7003        7013           2026-01-01  2026-12-31  eagle     Catalyst         12      3.4       1.0

        Totals:
          Rows: 3
          Crux:
            Available Balance: 88,888.8 node hours
            Charged          : 13,580.1 node hours
            Jobs             : 2,221
        Date filters (UTC): none
        '''
    sbank_default_layout = '''
         Id         Start       End         Resource   Project          Jobs        Charged          Available Balance
         ---------  ----------  ----------  ---------  ---------------  ----------  ---------------  -----------------
         42         2026-01-01  2026-12-31  somehost   Defaultlayout        5         1.0               2.0
        '''
    with patch("facilities.crux.compute.run_command",
               return_value=sbank_alcf_layout) as command:
        projects = backend.get_projects()
    assert command.call_args.args[1] == "sbank-list-allocations -r crux"
    assert [p["account"] for p in projects] == ["Catalyst", "My Project"]
    assert projects[0]["allocation_ids"] == ["7001"]    # eagle row filtered out
    assert projects[0]["jobs"] == 1234
    assert projects[0]["charged"] == 12345.6
    assert projects[1]["available_balance"] == 1234.5   # negative parsed, not rejected

    # sbank's documented default layout (no Suballocation column) is
    # the fallback shape — parsed, then resource-filtered like any other.
    with patch("facilities.crux.compute.run_command",
               return_value=sbank_default_layout):
        assert backend._allocations() == []

    with patch("facilities.crux.compute.run_command",
               return_value=sbank_alcf_layout):
        allocations = backend.get_project_allocations("My Project")
    assert allocations["charged_total"] == -1234.5
    assert allocations["allocations"][0]["end"] == "2026-12-31"
    assert allocations["allocations"][0]["suballocation_id"] == "7012"
    try:
        with patch("facilities.crux.compute.run_command",
                   return_value=sbank_alcf_layout):
            backend.get_project_allocations("NotAProject")
    except ValueError:
        pass
    else:
        raise AssertionError("crux invented allocations for an unknown project")

    # An empty sbank result is never a valid "no projects" answer: an
    # ALCF account always belongs to a project, so say what to check.
    with patch("facilities.crux.compute.run_command", return_value=""):
        try:
            backend.get_projects()
        except ValueError as exc:
            assert "expired" in str(exc) or "sbank" in str(exc)
        else:
            raise AssertionError(
                "crux returned an empty project list without explaining it")

    # qstat -Q table -> queue rows (header-driven parsing).
    queue_output = '''
        Queue          Max  Tot Ena Str Que Run Hld Wat Trn Ext Type
        -------------- ---- ---- --- --- ---- ---- ---- ---- ---- ---- ----
        debug            4    2 yes yes   1   1   0   0   0   0 Exe*
        workq-route    100   42 yes yes  12  30   0   0   0   0 Rou*
        '''
    # pbsnodes summary -> machine-wide node counts, including a
    # combined state (offline,resv-exclusive) that must not be skipped.
    nodes_output = '''
                                                                        mem       ncpus   nmics   ngpus
        vnode           state           njobs   run   susp      f/t        f/t     f/t     f/t   jobs
        --------------- --------------- ------ ----- ------ ------------ ------- ------- ------- -------
        x1000c0s0b1n0   free                0     0      0  252gb/252gb   256/256     0/0     0/0 --
        x1000c0s0b1n1   job-exclusive       1     1      0  252gb/252gb   256/256     0/0     0/0 7004
        x1000c0s0b1n2   offline             0     0      0  252gb/252gb   256/256     0/0     0/0 --
        x1000c0s0b1n3   offline,resv-exclusive 0  0      0  252gb/252gb   256/256     0/0     0/0 --
        '''
    with patch("facilities.crux.compute.run_command",
               side_effect=[queue_output, nodes_output]):
        resources_list = backend.get_live_resources()
    by_name = {row["partition"]: row for row in resources_list}
    assert set(by_name) == {"debug", "workq-route"}
    # live qstat -Q truncates the Type column ("Rou*"); the star is stripped
    assert by_name["workq-route"]["queue_type"] == "Rou"
    assert by_name["workq-route"]["running_jobs"] == 30
    assert by_name["debug"]["queued_jobs"] == 1
    nodes = by_name["debug"]["nodes"]
    assert nodes == {"allocated": 1, "idle": 1, "other": 2, "total": 4}

    # Down-node listing.
    down_output = ("x1000c0s0b1n2       offline    synthetic fixture reason\n")
    with patch("facilities.crux.compute.run_command",
               return_value=down_output):
        drained = backend.get_drained_nodes()
    assert drained == [{"nodes": "x1000c0s0b1n2", "state": "offline",
                         "reason": "synthetic fixture reason"}]

    # Submission: script written under the jobs dir, qsub run from there
    # so streamed output lands beside the script, id parsed off the
    # server-qualified form.
    submitted = spec_with()
    with patch("facilities.crux.compute.write_remote_file",
               return_value="/home/u/agent/jobs/crux-shape-20261101-000000.pbs") \
            as write, patch("facilities.crux.compute.run_command",
               return_value="7005.crux-pbs-0001.head.cm.crux.alcf.anl.gov\n") \
            as command:
        result = backend.submit(submitted)
    assert result["job_id"] == "7005.crux-pbs-0001.head.cm.crux.alcf.anl.gov"
    assert write.call_args.args[1].startswith("agent/jobs/")
    qsub_command = command.call_args.args[1]
    assert "chmod 600" in qsub_command
    assert 'cd "$(dirname' in qsub_command
    assert qsub_command.endswith("qsub /home/u/agent/jobs/crux-shape-20261101-000000.pbs")

    # Recent statuses: qselect (with the ctime cutoff) then batched
    # qstat for full records.
    with patch("facilities.crux.compute.run_command",
               side_effect=[
                   "7004.crux-pbs-0001.head.cm.crux.alcf.anl.gov\n"
                   "7005.crux-pbs-0001.head.cm.crux.alcf.anl.gov\n",
                   qstat_output,
               ]) as command:
        recent = backend.get_recent_statuses("now-2days")
    assert command.call_args_list[0].args[1].startswith("qselect -x -u $USER -tc.gt.")
    assert "qstat -fx -F JSON" in command.call_args_list[1].args[1]
    assert [job.id for job in recent] == ["7004.crux-pbs-0001.head.cm.crux.alcf.anl.gov"]

    # Update: qalter for mutable attributes, qmove for the queue, both
    # only for fields the caller actually set.
    updated = Job(id="7004", status={"state": "active"})
    walltime_spec = JobSpec(executable="true", attributes={"duration": 300})
    with patch("facilities.crux.compute.run_command") as command, \
            patch.object(backend, "get_statuses", return_value=[updated]):
        backend.update("7004", walltime_spec)
    assert command.call_args.args[1] == "qalter -l walltime=00:05:00 7004"

    move_spec = JobSpec(executable="true",
                        attributes={"queue_name": "debug"})
    with patch("facilities.crux.compute.run_command") as command, \
            patch.object(backend, "get_statuses", return_value=[updated]):
        backend.update("7004", move_spec)
    assert command.call_args.args[1] == "qmove debug 7004"

    everything_spec = JobSpec(executable="true", name="renamed",
                              stdout_path="logs/out.txt",
                              attributes={"duration": 300, "account": "Catalyst"},
                              resources={"node_count": 2})
    with patch("facilities.crux.compute.run_command") as command, \
            patch.object(backend, "get_statuses", return_value=[updated]):
        backend.update("7004", everything_spec)
    rendered = command.call_args.args[1]
    assert rendered.startswith("qalter ")
    assert "-N renamed" in rendered
    assert "-l walltime=00:05:00" in rendered
    assert "-A Catalyst" in rendered
    assert "-l select=2:system=crux" in rendered
    assert "-o logs/out.txt" in rendered

    conflicting = JobSpec(executable="true", attributes={
        "queue_name": "debug", "reservation_id": "A123456"})
    try:
        backend.update("7004", conflicting)
    except ValueError:
        pass
    else:
        raise AssertionError("crux updated toward two different queues at once")

    empty_spec = JobSpec(executable="true", environment={"A": "b"})
    try:
        backend.update("7004", empty_spec)
    except ValueError as exc:
        assert "Nothing updatable" in str(exc)
        assert "environment" in str(exc)
    else:
        raise AssertionError("crux applied an update with nothing updatable set")
