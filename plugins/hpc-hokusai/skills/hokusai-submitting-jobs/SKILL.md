---
name: hokusai-submitting-jobs
description: Use when the user wants to run, submit, or launch a job on HOKUSAI BigWaterfall2 (HBW2). Covers partition/queue selection, JobSpec construction, submission, and known gotchas specific to this facility.
user-invocable: true
---

# Submitting jobs on HOKUSAI BigWaterfall2 (HBW2)

Every tool call in this skill uses `facility="hokusai"`.

HBW2 is **CPU-first** — think CPU + MPI before reaching for GPUs. Describe
a job in resource terms and let the plugin assemble the sbatch script; you
never write sbatch flags by hand.

## Defaults applied for you

If omitted: partition → `mpc`, account → your configured `defaults.account`,
duration → 1 hour. **An account is mandatory on every HBW2 job** — if none
is set anywhere, `submit_job` errors telling you to name a project rather
than letting sbatch reject it. Call `get_projects(facility="hokusai")` to
see which accounts you may charge, and `hokusai-configuring` to set a
default.

## Choosing a partition

| partition | nodes | max nodes/job | max wall | default mem/core | use for |
|---|---|---|---|---|---|
| `mpc` (default) | 312 | 64 | 24 h | 1 GiB | everyday CPU / MPI |
| `mpc_l` | 156 | 8 | 72 h | 1 GiB | runs that cannot be split into ≤ 24 h pieces |
| `lmc` | 2 | 2 | 24 h | 30 GiB | more than 112 GiB on one node (2880 GiB nodes, 96 cores) |
| `gpu` | 3 | 1 | 72 h | 4 GiB | GPU batch (4 × NVIDIA H100 per node) |
| `gpu_i` | 1 | 1 | 24 h | 4 GiB | interactive GPU, CUDA compiles |

- `mpc_l` is the first 156 `mpc` nodes, not separate hardware, and backfill
  only plans 24 h ahead, so long jobs find gaps less easily. Prefer `mpc`
  with restarts.
- Per-**project** caps (shared by everyone billing the same account):
  7168 running cores and 5000 submitted jobs on MPC; 192 cores / 100 jobs
  on LMC; 448 cores / 100 jobs on GPU.
- **A job over a partition limit is accepted and never runs**
  (`EnforcePartLimits=NO`). It sits pending with reason
  `PartitionTimeLimit` or `PartitionNodeLimit` until cancelled. Stay inside
  the table above.
- A job may run up to 30 minutes past its time limit before it is killed
  (`OverTimeLimit=30`); budget for that in cost estimates, not in `duration`.
- Maintenance reservations hold whole partitions. A job whose time limit
  crosses the start of one stays pending with `ReqNodeNotAvail, Reserved
  for maintenance`; check `scontrol show reservation` with `run_command`
  before submitting anything long.

Call `get_resources(facility="hokusai")` to see where a job will start
soonest; `get_facility(facility="hokusai")` for full static limits.

## Memory and GPUs are billed as cores

Billing is the **maximum** of the weighted resources (`PriorityFlags=MAX_TRES`):

- `mpc`/`mpc_l`: max(cores, GiB requested)
- `lmc`: max(cores, GiB / 30)
- `gpu`/`gpu_i`: max(cores, GiB / 4, 28 × GPUs)

So on `mpc`, `resources.memory` of 112 GiB with one process costs a whole
node. Leave `resources.memory` unset unless a rank genuinely needs more than
1 GiB per core; a memory-hungry code should use fewer ranks per node (and
pay for the idle cores) or move to `lmc`. After a job runs, `sacct`'s
`AllocTRES` shows `billing=` next to `cpu=`; a gap between them means memory
inflated the cost.

Nodes are **shared core by core** between jobs (`OverSubscribe=NO`,
`CR_CORE_MEMORY`, cgroups). A job owns a node only if it asks for all 112
cores (or `exclusive_node_use`). Hyper-threading is off on compute nodes, so
one CPU is one physical core.

## The resource model

- `executable` — the command line (may be a full shell line, e.g.
  `module load intel && ./app`).
- `launcher` — the MPI launch prefix; see the next section for the exact
  string on HBW2. Under Slurm, `srun` inherits the job's allocation, so you
  do **not** pass process counts on the launch line.
- `resources.node_count`, `resources.processes_per_node` (MPI ranks/node),
  `resources.cpu_cores_per_process` (threads/rank), `resources.memory`
  (bytes/node), `resources.gpus` (job-total GPU count).
- `attributes.queue_name` (partition), `attributes.duration` (HH:MM:SS or
  seconds), `attributes.account` (project to bill).
- `environment` — env vars; **set `OMP_NUM_THREADS`** to match
  `cpu_cores_per_process` for threaded code (and to `1` for pure MPI), or a
  threaded library such as MKL starts one thread per core in every rank.
- Keep ranks × threads per node ≤ 112 on MPC and GPU nodes, ≤ 96 on LMC.

## Launching: `srun` needs `--cpus-per-task` repeated

HBW2 runs **Slurm 23.02**. Since 22.05, `srun` no longer inherits
`--cpus-per-task` from the batch allocation, and the plugin's sbatch header
sets it only on the `#SBATCH` line. With a bare `launcher="srun"`, every
rank of a threaded or hybrid job is bound to **a single core** and its
threads pile up on it. For any job with `cpu_cores_per_process` > 1, put the
value in the launcher:

```json
"launcher": "srun --cpus-per-task=8",
"resources": {"node_count": 2, "processes_per_node": 14, "cpu_cores_per_process": 8},
"environment": {"OMP_NUM_THREADS": "8"}
```

Pure MPI (`cpu_cores_per_process` unset or 1) is fine with plain `srun`.

## MPI / toolchain

Intel oneAPI is the default: `module load intel` (default `intel/25.3.0`)
brings the Intel compilers and Intel MPI together. Plain `srun` starts Intel
MPI ranks correctly (verified on 2 nodes: one correctly sized job, each rank
bound to its own core) because the job environment sets
`I_MPI_PMI_LIBRARY=/usr/lib64/libpmi.so`, even though `MpiDefault=none`.

Open MPI (`openmpi/4.1.6`, with `gcc/13.2.0`) is available but **conflicts
with Intel MPI** — load one only (`module purge` before switching). For a
binary built against the site Open MPI, use **`launcher="srun --mpi=pmix"`**
(verified with a 2-node CP2K run, each rank bound to its own cores). Plain
`srun` without `--mpi=pmix` has not been verified with Open MPI. Unlike RIKYU
and R-CCS Cloud, `srun` (not `mpirun`) is the correct launcher here for both
MPI flavours.

The default `intel/25.3.0` ships **only the LLVM compilers**
(`icx`/`icpx`/`ifx` and the `mpiicx`/`mpiicpx`/`mpiifx` wrappers). The
classic wrappers (`mpiicc`/`mpiicpc`/`mpiifort`) are also on `PATH`, but
their backing compilers (`icc`/`icpc`/`ifort`) are not installed under
25.3.0 — a build system that names them fails at configure time. New build
recipes should use the LLVM names. A code that insists on the classic
compilers can use **`module load intel/23.02.1`** (Intel MPI 2021.10),
which still provides `ifort`/`icc`/`icpc` 2021.10 and a working `mpiifort`.

Other modules worth knowing: `gcc-toolset/15.1.1`, `cmake/3.30.1`,
`fftw-*/3.3.10`, `hdf5`, `phdf5`, NetCDF, `boost`, `cuda` 11.7–13.2 (default
13.2). There are **no modules for CP2K, libxc, libint, ELPA, PLUMED or a
standalone ScaLAPACK**: use MKL (`-qmkl`) for BLAS/LAPACK/ScaLAPACK/FFTW and
build the rest under home.

Job shells start with a clean environment: a binary built under
`module load intel` needs the **same `module load` in the job that runs
it**, or it fails at launch with exit 127 and an
`error while loading shared libraries` message (MKL, Intel MPI).

**Never add `export=NONE`** (e.g. through `attributes.custom_attributes`):
a job submitted that way cannot see the HOKUSAI module trees at all, so
every `module load` fails. The plugin's default submission (no `--export`
option) sees them.

## GPUs

Request GPUs with `resources.gpus` (a job-total count, one node at most:
1, 2 or 4). Each GPU brings **28 CPU cores and 112 GiB** and is billed as 28
cores. The 28 cores of a 1-GPU job are split 14 + 14 across the two sockets
(`0-13,56-69`), so it already spans two NUMA domains; pin threads with that
in mind. Containers get NVIDIA passthrough (`--nv`) automatically when the
job requests GPUs. Load the modules the program was built with (e.g.
`cuda/12.4`), not `intel`.

The GPU partitions are small and usually full: waits of many hours have
been seen even for a 1-GPU, 5-minute `gpu_i` job. Request GPUs only when the
work needs one, and don't promise the user an interactive GPU session will
start while they wait.

## Where files go

- `/tmp_work` is shared scratch, purged once files are unmodified for a
  week. It is **flat and world-writable**: create `/tmp_work/<user>` and work
  inside it.
- **GPU nodes do not mount `/tmp_work`.** A GPU job that changes into a
  `/tmp_work` path fails; stage GPU runs under home (e.g.
  `~/agent/work/<name>/`) or the node's `/tmp`.
- Node-local scratch is **`/tmp`** (`TMPDIR=/tmp` in jobs): about 105 GB on
  MPC nodes, 339 GB on GPU nodes. It is shared with other jobs on the node,
  so use a job-specific subdirectory, copy results out before the script
  ends, and remove it.
- Many small files hit the inode quota (10 million on home) before the
  block quota; tar per-step outputs.

## Containers

Set `container.image` to a `.sif` path or `docker://` URI to run inside
Singularity (singularity-ce 3.11); `launcher="srun"` stays outside the
container so MPI works.

## Internet from compute nodes

Compute nodes have no direct internet route. A job that must fetch
something reaches the web through the front-end proxy at
`http://$SLURM_SUBMIT_HOST:3128`. Export it in `pre_launch`, not in
`environment`:

```
export http_proxy=http://$SLURM_SUBMIT_HOST:3128 https_proxy=http://$SLURM_SUBMIT_HOST:3128
```

`environment` values are written single-quoted, so a `$VAR` in them is
passed literally and never expands.

## Before submitting

1. The account is set and `get_projects` shows budget left.
2. Partition, node count and `duration` are inside the table above (an
   over-limit job never starts).
3. `duration` is realistic: too short ends in TIMEOUT, far too long delays
   the start because backfill can't place it.
4. Threaded jobs have `OMP_NUM_THREADS` **and** `--cpus-per-task` on the
   `srun` launcher.
5. `resources.memory` is no larger than needed (it is billed as cores).
6. The job loads the same modules the binary was built with.
7. Tell the user the worst-case cost in core-hours (billed cores ×
   `duration`) for anything larger than a smoke test, and run a few-minute
   one-node test before scaling up.

## Universal rules (apply on every facility, not just this one)

- `submit_job(facility="hokusai", spec=spec)` — **show the user the spec
  (or a plain-language description of it) before submitting, unless
  they've explicitly said to just run it.** No exceptions for this call.
- The returned `{"job_id": ..., "script_path": ...}` — the rendered script
  is kept on the cluster; `fs_view(facility="hokusai", path=script_path)`
  to inspect exactly what was submitted.
- If a partition/queue/GPU choice isn't given or obvious, check
  `get_resources(facility="hokusai")` (live occupancy) and
  `get_facility(facility="hokusai")` (static limits) — don't guess.
- Don't run heavy computation on the login node — submit a job instead.
- Stage anything the cluster needs (source trees, inputs, uploads, run
  directories) under `~/agent/work/<descriptive-name>/`, never in the
  home-directory root. `~/agent/` is the agent's visible scratch area —
  `~/agent/jobs` already holds every submitted script — and the home
  root stays the user's own.
- Create a requested `spec.directory` with `fs_mkdir` **before** submission.
  Schedulers enter that directory before the script body and `pre_launch`
  runs, so a `mkdir` in `pre_launch` is too late. On Slurm, `sbatch` also
  runs from that directory, so `$SLURM_SUBMIT_DIR` is the run directory
  and a submission into a missing one fails. Other schedulers' submit-dir
  variables (`$PBS_O_WORKDIR`, `$SGE_O_WORKDIR`, ...) are not the run
  directory: a script there should rely on its working directory instead.
- Don't guess facility-specific details not covered by the notes above —
  use `search_docs(facility="hokusai", query=...)` on the `hpc`
  server. It searches a bundled guide, not a live site — never invent a URL
  to send the user to.
- Don't call `cancel_job` without confirming with the user first.
