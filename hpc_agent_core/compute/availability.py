"""Per-node free cores and memory: "can a partly used node take this job?"

Scheduler-neutral half of get_node_availability. A backend reads, for one
partition, its sharing policy and one row per node (cores allocated/idle/
other/total, memory total/allocated/OS-free in MB); summarise() then decides
which nodes can take a job of a given size and says why the others cannot.

Why it exists: get_resources counts whole nodes per partition, and a node with
some cores in use ("mixed") counts as allocated, so its idle cores are
invisible. Free cores are not enough either: on HOKUSAI mpc in November 2026 a
mixed node had 98 of 112 cores idle but only ~13 GB of memory left to
allocate, so a job asking for the 1 GiB/core default could not start there.
"""
from __future__ import annotations

#: Node states (Slurm's long names, lower case) a new job can start on. Any
#: suffix flag other than power saving ("~" powered down, "#" powering up)
#: means not now: "*" not responding, "!" pending power down, "%" powering
#: down, "$" maintenance reservation, "@" pending reboot, "-" planned by the
#: backfill scheduler.
ACCEPTING_STATES = frozenset({"idle", "mixed"})
_POWER_SAVE_SUFFIXES = "~#"
MB_PER_GIB = 1024


def _accepting(state: str) -> bool:
    return state.rstrip(_POWER_SAVE_SUFFIXES) in ACCEPTING_STATES


def summarise(partition: str, policy: dict, nodes: list[dict], *,
              min_free_cores: int = 0, min_free_memory_gb: float = 0,
              max_nodes: int = 30) -> dict:
    """Decide which nodes of *partition* can take a job of the given size.

    policy: {"whole_node": bool, "exclusive_user": bool,
             "max_mem_per_node_mb": int | None, "default_mem_per_cpu_mb": int | None,
             "source": str} — what the scheduler says about sharing nodes.
    nodes: [{"node", "state", "cores": {allocated, idle, other, total},
             "memory_mb": {total, allocated, os_free}}], one per node.

    A node matches when it accepts jobs, has at least the requested idle
    cores, and can still allocate the requested memory to one job:
    min(total - allocated, the partition's per-job-per-node cap). With no
    memory asked for, the job is taken to need the partition's default
    memory per core times its cores, which is what the scheduler gives a
    job that sets no --mem. On a whole-node partition only fully idle nodes
    match, since free cores on a mixed node cannot be given to anyone else.
    """
    if min_free_cores < 0 or min_free_memory_gb < 0:
        raise ValueError("min_free_cores and min_free_memory_gb must not be negative")
    if max_nodes < 0:
        raise ValueError("max_nodes must not be negative")
    cap = policy.get("max_mem_per_node_mb")
    whole_node = bool(policy.get("whole_node"))
    need_cores = max(1, int(min_free_cores))
    if min_free_memory_gb > 0:
        need_mem = int(round(min_free_memory_gb * MB_PER_GIB))
        basis = "min_free_memory_gb"
    elif policy.get("default_mem_per_cpu_mb"):
        need_mem = need_cores * policy["default_mem_per_cpu_mb"]
        basis = (f"no memory asked for: {need_cores} core(s) x the partition's default "
                 f"{policy['default_mem_per_cpu_mb']} MB per core (a job without --mem gets this)")
    else:
        need_mem = 0
        basis = "no memory asked for and the partition sets no default per core"

    by_state: dict[str, int] = {}
    rows, matching = [], []
    idle_cores_on_idle = idle_cores_on_mixed = 0
    fully_idle = mixed = accepting = short_of_memory = 0
    best_cores = best_mem = 0
    memory_tracked = False
    for n in nodes:
        state = n["state"]
        by_state[state] = by_state.get(state, 0) + 1
        c, m = n["cores"], dict(n["memory_mb"])
        schedulable = max(0, m["total"] - m["allocated"])
        m["schedulable"] = schedulable
        m["max_for_one_job"] = min(schedulable, cap) if cap else schedulable
        if m["allocated"] > 0:
            memory_tracked = True
        row = {"node": n["node"], "state": state, "cores": c, "memory_mb": m}
        rows.append(row)
        if not _accepting(state):
            continue
        accepting += 1
        base = state.rstrip(_POWER_SAVE_SUFFIXES)
        if base == "idle":
            fully_idle += 1
            idle_cores_on_idle += c["idle"]
        else:
            mixed += 1
            idle_cores_on_mixed += c["idle"]
        best_cores = max(best_cores, c["idle"])
        best_mem = max(best_mem, m["max_for_one_job"])
        if whole_node and base != "idle":
            continue
        if c["idle"] < need_cores:
            continue
        if m["max_for_one_job"] < need_mem:
            short_of_memory += 1
            continue
        matching.append(row)

    matching.sort(key=lambda r: (-r["cores"]["idle"], -r["memory_mb"]["max_for_one_job"], r["node"]))
    notes = []
    if whole_node:
        notes.append("This partition allocates whole nodes (OverSubscribe=EXCLUSIVE): idle cores on "
                     "a mixed node cannot be given to another job, so only fully idle nodes match.")
    if policy.get("exclusive_user"):
        notes.append("ExclusiveUser=YES: a mixed node only takes more jobs from the user already "
                     "running there.")
    if cap:
        notes.append(f"One job may use at most {cap} MB ({cap / MB_PER_GIB:g} GiB) per node here "
                     "(MaxMemPerNode), even where a node has more free.")
        if need_mem > cap:
            notes.append(f"The {need_mem} MB asked for exceeds that per-node cap: no node can ever "
                         "match; spread the job over more nodes or ask for less memory.")
    if any(r["cores"]["allocated"] > 0 for r in rows) and not memory_tracked:
        notes.append("No node reports allocated memory although cores are in use: memory may not "
                     "be a scheduled resource here, so 'schedulable' is only an upper bound.")
    notes.append("A job with exclusive_node_use needs a fully idle node whatever the policy.")
    notes.append("A snapshot: higher-priority waiting jobs may take these cores first. "
                 "memory_mb.os_free is what the OS reports unused, not what can be allocated.")
    return {
        "partition": partition,
        "policy": policy,
        "required": {"cores": need_cores, "memory_mb": need_mem, "memory_basis": basis},
        "summary": {
            "nodes": len(rows),
            "by_state": by_state,
            "accepting_jobs": accepting,
            "fully_idle_nodes": fully_idle,
            "mixed_nodes": mixed,
            "idle_cores_on_idle_nodes": idle_cores_on_idle,
            "idle_cores_on_mixed_nodes": idle_cores_on_mixed,
            "most_idle_cores_on_one_node": best_cores,
            "most_memory_mb_for_one_job_on_one_node": best_mem,
            "matching_nodes": len(matching),
            "nodes_with_the_cores_but_short_of_memory": short_of_memory,
        },
        "nodes": matching[:max_nodes],
        "nodes_listed": min(len(matching), max_nodes),
        "notes": notes,
    }
