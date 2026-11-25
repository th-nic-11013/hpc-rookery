from unittest.mock import patch

LABEL = 'Slurm status reports requested memory, peak RSS and the batch script'


def slurm_status_reports_memory_and_script():
    """Job status carries meta_data req_mem (sacct ReqMem), max_rss (the
    largest MaxRSS of the job's steps) and script_path (the script on its
    sbatch SubmitLine). The UiO-66 campaign spent ~8 run_command calls
    digging these out, e.g. to see that OOM-killed job 9895283 had asked for
    1G. Lines below are HOKUSAI's real sacct output for 9895283 and 9922244
    (Slurm 23.02.6). An older sacct that refuses SubmitLine ("Invalid field
    requested") gets the base query, once, and status still works."""
    from hpc_agent_core.compute import slurm
    from hpc_agent_core.models import JobState
    base = "|mpc|{state}|00:01:00|2026-11-24T11:53:10|2026-11-24T11:54:10|0:0|mpc0001|/home/u/w"
    sacct = "\n".join([
        "9895283|eval-r2" + base.format(state="OUT_OF_MEMORY")
        + "|1G||sbatch --parsable --chdir=/home/u/agent/work/eval-r2 /lustre/home/u/agent/jobs/eval-r2-20261124-115307.sh",
        "9895283.batch|batch" + base.format(state="OUT_OF_MEMORY") + "||581456K|",
        "9895283.extern|extern" + base.format(state="COMPLETED") + "||104K|",
        "9922244|r3-s1.00" + base.format(state="COMPLETED")
        + "|56G||sbatch --parsable --chdir=/home/u/r3/s1.00 /lustre/home/u/agent/jobs/r3-s1.00-20261124-172733.sh",
        "9922244.batch|batch" + base.format(state="COMPLETED") + "||5376K|",
        "9922244.0|lmp_mpi" + base.format(state="COMPLETED")
        + "||7600400K|srun --mpi=pmix --ntasks=1 --cpus-per-task=56 lmp_mpi -in in.npt.lmp | tee x",
    ]) + "\n"
    backend = slurm.SlurmBackend("test-slurm", has_accounting=True)
    with patch("hpc_agent_core.compute.slurm.run_command", side_effect=[sacct]) as rc:
        jobs = {j.id: j for j in backend.get_statuses(["9895283", "9922244"])}
    assert "ReqMem,MaxRSS,SubmitLine" in rc.call_args_list[0].args[1], rc.call_args_list
    oom = jobs["9895283"].status
    assert oom.state == JobState.FAILED and oom.meta_data["native_state"] == "OUT_OF_MEMORY"
    assert oom.meta_data["req_mem"] == "1G", oom.meta_data
    assert oom.meta_data["max_rss"] == "567.8M", oom.meta_data
    assert oom.meta_data["script_path"] == "/lustre/home/u/agent/jobs/eval-r2-20261124-115307.sh"
    md = jobs["9922244"].status.meta_data
    assert (md["req_mem"], md["max_rss"]) == ("56G", "7.2G"), md
    assert md["script_path"].endswith("r3-s1.00-20261124-172733.sh"), md
    assert set(jobs) == {"9895283", "9922244"}   # steps are not jobs

    # an older sacct refuses the usage fields: one retry without, remembered
    old = slurm.SlurmBackend("test-slurm-old", has_accounting=True)
    plain = "11|a|mpc|COMPLETED|00:01:00|2026-11-06T10:00:00|2026-11-06T10:01:00|0:0|n1|/w\n"
    refusal = RuntimeError('sacct: error: Invalid field requested: "SubmitLine"')
    with patch("hpc_agent_core.compute.slurm.run_command", side_effect=[refusal, plain, plain]) as rc:
        first = old.get_statuses(["11"])[0]
        again = old.get_statuses(["11"])[0]
    assert first.status.state == JobState.COMPLETED and "req_mem" not in first.status.meta_data
    assert again.status.state == JobState.COMPLETED
    assert rc.call_count == 3 and "SubmitLine" not in rc.call_args_list[2].args[1], rc.call_args_list
    # any other failure is not mistaken for an old sacct
    with patch("hpc_agent_core.compute.slurm.run_command",
               side_effect=RuntimeError("ssh: connection refused")):
        try:
            slurm.SlurmBackend("t3", has_accounting=True).get_statuses(["11"])
        except RuntimeError as e:
            assert "connection refused" in str(e)
        else:
            raise AssertionError("an SSH failure was swallowed")
    assert slurm._script_from_submit_line("srun ./a.out") is None
    assert slurm._script_from_submit_line("sbatch --wrap") is None
