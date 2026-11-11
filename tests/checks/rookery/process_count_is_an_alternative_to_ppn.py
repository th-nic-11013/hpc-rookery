from hpc_agent_core.models import JobSpec

LABEL = 'process_count does not conflict with default tasks-per-node'


def process_count_is_an_alternative_to_ppn():
    from hpc_agent_core.compute.slurm import SlurmBackend

    backend = SlurmBackend("test", nodes_always_explicit=True)
    total_only = JobSpec(
        executable="hostname", resources={"process_count": 8},
        attributes={"queue_name": "test"},
    )
    script = backend.render_script(total_only)
    assert "#SBATCH --ntasks=8" in script
    assert "#SBATCH --ntasks-per-node" not in script, script

    both = JobSpec(
        executable="hostname",
        resources={"process_count": 8, "processes_per_node": 4},
        attributes={"queue_name": "test"},
    )
    script = backend.render_script(both)
    assert "#SBATCH --ntasks=8" in script
    assert "#SBATCH --ntasks-per-node=4" in script
