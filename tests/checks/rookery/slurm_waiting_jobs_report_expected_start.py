from unittest.mock import patch

LABEL = 'Slurm waiting jobs report expected_start (G31)'


def slurm_waiting_jobs_report_expected_start():
    """G31: a waiting Slurm job carries squeue's %S estimate as
    meta_data["expected_start"]; N/A / Unknown never read as a time."""
    from hpc_agent_core.compute import slurm
    from hpc_agent_core.models import JobState
    sacct = ("11|a|mpc|PENDING|00:00:00|Unknown|Unknown|0:0|None assigned|/w\n"
             "12|b|mpc|PENDING|00:00:00|Unknown|Unknown|0:0|None assigned|/w\n"
             "13|c|mpc|RUNNING|00:01:00|2026-11-06T10:00:00|Unknown|0:0|mpc0001|/w\n")
    squeue = "11|(Priority)|2026-11-07T03:12:00\n12|(Resources)|N/A\n"
    backend = slurm.SlurmBackend("test-slurm", has_accounting=True)
    with patch("hpc_agent_core.compute.slurm.run_command", side_effect=[sacct, squeue]):
        jobs = {j.id: j for j in backend.get_statuses(["11", "12", "13"])}
    assert jobs["11"].status.message == "(Priority)"
    assert jobs["11"].status.meta_data["expected_start"] == "2026-11-07T03:12:00"
    assert "expected_start" not in jobs["12"].status.meta_data, jobs["12"].status.meta_data
    assert jobs["13"].status.state == JobState.ACTIVE
    assert "expected_start" not in jobs["13"].status.meta_data
    # no-accounting path: the estimate comes in the primary squeue query
    live = slurm._parse_squeue("21|PENDING|(Priority)|mpc|/w|2026-11-07T03:12:00\n"
                               "22|PENDING|(Priority)|mpc|/w|Unknown\n"
                               "23|RUNNING|mpc0002|mpc|/w|2026-11-06T10:00:00\n"
                               "24|PENDING|(Priority)|mpc|/w\n")   # old 5-field format
    meta = {j.id: j.status.meta_data for j in live}
    assert meta["21"]["expected_start"] == "2026-11-07T03:12:00"
    assert all("expected_start" not in meta[i] for i in ("22", "23", "24")), meta
