# HOKUSAI BigWaterfall2 (HBW2) values for the CP2K benchmark

`SKILL.md` describes the method in terms that hold on any Slurm machine.
This file gives the HOKUSAI BigWaterfall2 (HBW2) values. Measured timings are not here:
they are `benchmarks` records, read through `hokusai-records`.

## Node

| item | value |
|---|---|
| partition | `mpc` (default; 64 nodes and 24 h per job), `mpc_l` (8 nodes, 72 h) |
| `node_type` in records | `MPC (bwmpc), 112 cores` |
| cores per node | 112, in **8 NUMA domains of 14 cores** |
| memory | ~16 GB HBM per NUMA domain, no DDR; leave `resources.memory` unset (above 1 GiB/core is billed as extra cores) |
| full-node scan layouts (`SCAN_LAYOUTS`) | `112x1 56x2 28x4 16x7 8x14 4x28` (R × T = 112) |
| smoke layouts (`SMOKE_LAYOUTS`) | `28x4 8x14` |

A full-node layout `R x T` has R × T = 112. With T = 1, 2, 7 or 14 every
rank stays inside one NUMA domain. With T = 28 (`4x28`), each rank spans
exactly two whole domains. With T = 4 (`28x4`), block binding splits some
ranks across a domain boundary. Both were measured anyway; read them as data
points, not as clean layouts.

## Launch and binding

```bash
srun --mpi=pmix -n R -c T --cpu-bind=cores --distribution=block:block
export OMP_NUM_THREADS=T OMP_PLACES=cores OMP_PROC_BIND=close
```

- `--mpi=pmix` is required: the builds use the site Open MPI.
- `--cpus-per-task` must be repeated on `srun`, because Slurm 23.02 does not
  inherit it.
- With these flags, rank r owns cores r×T to r×T+T−1.

## Scheduler behaviour that shapes the job

- **`OverTimeLimit=30`:** a job may run 30 min past `--time`. Hence the
  per-run `timeout` and the guard that never starts a run that could
  outlast `BENCH_LIMIT_S`.
- **Exclusive nodes are rare:** often 0 of 312 MPC nodes are idle, so an
  exclusive job can wait hours with reason `Resources`. Tell the user.
- A job over a partition limit is accepted but never starts.

## Front end

- Python for the scripts: `/usr/bin/python3.12 -I`. It has numpy (1.24.4,
  checked 2026-11-03), which `make_cp2k_md_input.py` needs. `uv` and
  `python3.12 -m venv` also work there, for anything more.
- Parse-check an input in a scratch copy:
  `( source <env_script>; OMP_NUM_THREADS=1 timeout 60 cp2k.psmp --check -i NAME.inp )`.
- `<env_script>` (and `CP2K_ENV` in the job scripts) is the
  `env_script` of the build being timed, from `software_registry`; the
  scripts have no default build.

## The benchmark job spec

```json
{
  "name": "cp2k-bench-<system>",
  "directory": "/home/<user>/agent/work/cp2k-runs/<system>/bench",
  "executable": "bash bench_job.sh",
  "resources": {"node_count": 1, "processes_per_node": 112, "cpu_cores_per_process": 1,
                "exclusive_node_use": true},
  "attributes": {"queue_name": "mpc", "duration": "00:53:00"}
}
```

- `directory` must be absolute: it is passed to `sbatch --chdir`, which does
  not expand `~`.
- `BENCH_LIMIT_S` in `bench_job.sh` must equal `duration` in seconds (3180).
- **Cost:** nodes × 112 × duration in hours, at most 98.9 core-h for this
  spec. The jobs run so far took 5–12 min (10–22 core-h). A single scan run
  took 17–32 s for 192-atom water and 86–103 s for the 114-atom MOF.
- The scripts' own `#SBATCH` lines (`--partition=mpc`, `--account=RBxxxxxx`)
  are for a manual `sbatch` only. Under `submit_job` the spec sets the
  resources.

## Values the scripts are rendered with

`facilities/hokusai/codes/cp2k/values.json` in hpc-rookery fills these into
the scripts and `SKILL.md`:
- partition `mpc`;
- 112 cores per node (`parse_bench.py`'s `NODE_CORES`, so
  its speed-up column is against `112x1`);
- scan layouts `112x1 56x2 28x4 16x7 8x14 4x28` and smoke layouts
  `28x4 8x14`;
- `/usr/bin/python3.12` and `srun --mpi=pmix`.

`multinode_layouts_job.sh` has no default `LAYOUTS` or `REF_LAYOUT`: take
them from the `benchmarks` records for the system.
