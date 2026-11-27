from unittest.mock import patch

LABEL = 'Node availability sees idle cores and allocatable memory on mixed nodes'

# HOKUSAI's real `scontrol show partition -o mpc` fields (Slurm 23.02.6,
# 2026-11-27), trimmed, and sinfo rows in the tool's -O field order. bwmpc001
# is real: 98 of 112 cores idle but 114688 of 128300 MB allocated.
POLICY = ("PartitionName=mpc AllowGroups=ALL Default=YES ExclusiveUser=NO MaxNodes=64 "
          "Nodes=bwmpc[001-312] OverSubscribe=NO State=UP TotalCPUs=34944 "
          "DefMemPerCPU=1024 MaxMemPerNode=114688 TRESBillingWeights=CPU=1,Mem=1G")
ROWS = "\n".join([
    "bwmpc001|mixed|14/98/0/112|128300|114688|117914|",
    "bwmpc002|mixed|59/53/0/112|128300|120832|121559|",
    "bwmpc003|idle|0/112/0/112|128300|0|125000|",
    "bwmpc004|allocated|112/0/0/112|128300|114688|20000|",
    "bwmpc005|drained|0/0/112/112|128300|0|N/A|",
    "bwmpc006|idle*|0/112/0/112|128300|0|N/A|",
    "bwmpc007|mixed|8/104/0/112|128300|8192|110000|",
    "bwmpc001|mixed|14/98/0/112|128300|114688|117914|",   # listed twice
]) + "\n"


def _run(backend, policy=POLICY, rows=ROWS, **kw):
    out = f"{policy}\n@@ROOKERY_NODES@@\n{rows}"
    with patch("hpc_agent_core.compute.slurm.run_command", return_value=out) as rc:
        r = backend.get_node_availability("mpc", **kw)
    return r, rc.call_args.args[1]


def node_availability_sees_memory_on_mixed_nodes():
    """get_resources counts a mixed node as allocated; get_node_availability
    looks inside it. A node with idle cores but its memory allocated must
    not be offered for a job that needs that memory, the per-job memory cap
    (MaxMemPerNode) applies, a whole-node partition offers only idle nodes,
    and an odd partition name never reaches the shell."""
    from hpc_agent_core.compute import slurm
    b = slurm.SlurmBackend("test-slurm")

    # 8 cores, no memory asked: the partition default (1024 MB/core) applies
    r, cmd = _run(b, min_free_cores=8)
    assert "scontrol show partition -o mpc" in cmd and "sinfo -N -p mpc --noheader" in cmd, cmd
    assert "NodeList:|,StateLong:|,CPUsState:|,Memory:|,AllocMem:|,FreeMem:|" in cmd, cmd
    assert r["required"] == {"cores": 8, "memory_mb": 8192, "memory_basis": r["required"]["memory_basis"]}
    p = r["policy"]
    assert (p["whole_node"], p["exclusive_user"], p["max_mem_per_node_mb"], p["default_mem_per_cpu_mb"]) \
        == (False, False, 114688, 1024), p
    s = r["summary"]
    assert s["nodes"] == 7, "bwmpc001 listed twice is one node"
    assert s["by_state"] == {"mixed": 3, "idle": 1, "allocated": 1, "drained": 1, "idle*": 1}, s
    assert (s["accepting_jobs"], s["fully_idle_nodes"], s["mixed_nodes"]) == (4, 1, 3), s
    assert s["idle_cores_on_mixed_nodes"] == 98 + 53 + 104 and s["idle_cores_on_idle_nodes"] == 112, s
    # bwmpc002 has 53 idle cores but only 7468 MB left: short of memory
    names = [n["node"] for n in r["nodes"]]
    assert names == ["bwmpc003", "bwmpc007", "bwmpc001"], names
    assert s["nodes_with_the_cores_but_short_of_memory"] == 1, s
    n1 = next(n for n in r["nodes"] if n["node"] == "bwmpc001")
    assert n1["memory_mb"] == {"total": 128300, "allocated": 114688, "schedulable": 13612,
                               "max_for_one_job": 13612, "os_free": 117914}, n1
    n3 = r["nodes"][0]
    assert n3["memory_mb"]["max_for_one_job"] == 114688, "MaxMemPerNode caps one job"
    assert s["most_memory_mb_for_one_job_on_one_node"] == 114688, s

    # the user's case: 8 cores with 56 GiB -> only the idle and the light mixed node
    r, _ = _run(b, min_free_cores=8, min_free_memory_gb=56)
    assert [n["node"] for n in r["nodes"]] == ["bwmpc003", "bwmpc007"], r["nodes"]
    assert r["summary"]["nodes_with_the_cores_but_short_of_memory"] == 2

    # more than the per-node cap can never fit, and the result says why
    r, _ = _run(b, min_free_memory_gb=120)
    assert r["nodes"] == [] and any("exceeds that per-node cap" in n for n in r["notes"]), r["notes"]

    # max_nodes trims the list, not the count
    r, _ = _run(b, max_nodes=1)
    assert r["nodes_listed"] == 1 and r["summary"]["matching_nodes"] == 4, r["summary"]

    # whole-node partition: free cores on a mixed node are unusable
    r, _ = _run(b, policy=POLICY.replace("OverSubscribe=NO", "OverSubscribe=EXCLUSIVE"))
    assert r["policy"]["whole_node"] is True
    assert [n["node"] for n in r["nodes"]] == ["bwmpc003"], r["nodes"]
    assert any("whole nodes" in n for n in r["notes"]), r["notes"]

    # no memory accounting at all: say schedulable is only an upper bound
    flat = "n1|mixed|10/102/0/112|128300|0|90000|\n"
    r, _ = _run(b, policy="PartitionName=x OverSubscribe=YES:4", rows=flat)
    assert r["policy"]["max_mem_per_node_mb"] is None and r["required"]["memory_mb"] == 0
    assert any("upper bound" in n for n in r["notes"]), r["notes"]

    # odd partition names are refused before any SSH
    for bad in ["mpc; rm -rf ~", "mpc,gpu", "$(id)", "mpc mpc_l", "", "a'b", "x" * 65]:
        with patch("hpc_agent_core.compute.slurm.run_command") as rc:
            try:
                b.get_node_availability(bad)
            except ValueError as e:
                assert "Invalid partition name" in str(e)
            else:
                raise AssertionError(f"accepted partition {bad!r}")
            assert not rc.called, bad
    for bad in [{"min_free_cores": -1}, {"min_free_memory_gb": -2}, {"max_nodes": -1}]:
        try:
            _run(b, **bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted {bad}")

    # an unknown partition is a clear error; other failures pass through
    with patch("hpc_agent_core.compute.slurm.run_command",
               side_effect=RuntimeError("Partition nope not found")):
        try:
            b.get_node_availability("nope")
        except ValueError as e:
            assert "get_resources" in str(e)
        else:
            raise AssertionError("unknown partition not reported")
    with patch("hpc_agent_core.compute.slurm.run_command",
               side_effect=RuntimeError("ssh: connection refused")):
        try:
            b.get_node_availability("mpc")
        except RuntimeError as e:
            assert "connection refused" in str(e)
        else:
            raise AssertionError("an SSH failure was swallowed")

    # a scheduler without the query says so plainly
    from hpc_agent_core.compute.base import SchedulerBackend

    class NoQuery(SchedulerBackend):
        render_script = submit = get_statuses = get_recent_statuses = cancel = lambda *a, **k: None
    try:
        NoQuery(facility="t", name="pjm").get_node_availability("small")
    except NotImplementedError as e:
        assert "get_node_availability" in str(e) and "pjm" in str(e), e
    else:
        raise AssertionError("base backend did not refuse")

    # the tool is served and points at get_resources and back
    from hpc_mcp import hpc_server
    doc = hpc_server.get_node_availability.__doc__ or ""
    assert "get_resources" in doc and "get_node_availability" in (hpc_server.get_resources.__doc__ or "")
