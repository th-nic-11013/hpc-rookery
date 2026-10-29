# HOKUSAI BigWaterfall2 (HBW2)

An original, plain-language orientation to the HOKUSAI BigWaterfall2 (HBW2)
system, written for users who drive it through HokusaiAgent. It records the
site-specific facts that shape how you ask for work — not general HPC/Linux
background, and not a command reference. Stable facts (partition names, node
shapes, paths) are stated here so the agent can size a job without a round-trip;
genuinely changing values (queue occupancy, your budget balance, exact installed
versions) are left to the live system, which the agent queries on demand.

## What HBW2 is

HBW2 is a CPU-first supercomputer at RIKEN, scheduled with Slurm and reached
through shared front-end login nodes. Three subsystems:

- **MPC (Massively Parallel Computer)** — the workhorse: 312 standard nodes, each
  with 112 Intel Xeon cores (two 56-core sockets) and ~112 GiB of memory. Most
  work runs here.
- **LMC (Large Memory)** — 2 nodes with 96 cores and 2880 GiB of usable
  memory each, for jobs that need very large memory on a single host.
- **GPU** — a small 4-node server, each node with 4 NVIDIA H100 GPUs, 112
  cores and 448 GiB, mainly for postprocessing. Secondary; most HBW2 work is
  CPU/MPI, and the GPU nodes are usually fully allocated.

Compute nodes have hyper-threading off, so one CPU is one physical core.

The practical consequence for working with the agent: think CPU and MPI first,
describe jobs in nodes/processes/threads, and reach for GPUs only when a workload
genuinely needs one.

## Getting on the system

You connect over SSH to `hokusai.riken.jp`, load-balanced across login nodes
`hokusai1`–`hokusai4`. Authentication is by SSH key only — no password prompts —
so register your public key through the web portal (https://hokusai.riken.jp/hbw2/)
before your first login. The portal also lists current application versions and
hosts the official manuals.

Login nodes are for editing, building, staging files, and submitting. They are
shared and lightly resourced (each user is limited to 256 processes and 48 GiB
of virtual memory on a front-end node), so anything heavier than a quick check
belongs in a job — have the agent submit it rather than running it on the front
end. Avoid recursive scans (`find`, `du`, `ls -R`) over whole shared file
systems; they load the Lustre metadata servers for every user.

## Projects and your compute budget

Every job is billed to a project and you must name one when you submit (the
scheduler flag is `--account`). Project IDs are short codes: RIKEN projects begin
`RB` (e.g. `RB99999`); HPCI projects derive from an `HP` code. The agent can hold
a default project and let you override it per job.

Each project has a finite core-time allowance, spent as jobs run and recovering
gradually under a fair-share policy — so steady use through the year beats bursts,
and when the allowance is exhausted the project's new jobs stop starting. The
remaining balance changes continuously; ask the agent to read it live rather than
assuming it.

## Choosing where a job runs

A partition selects the subsystem and bounds wall time. The defaults if you don't
say otherwise: 1 hour of wall time, and a per-core memory share of 1 GiB on MPC,
30 GiB on LMC, 4 GiB on GPU (asking for more memory can raise the number of cores
you are billed for).

| partition | use for | nodes | max nodes/job | cores/node | memory/node | max wall time |
|---|---|---|---|---|---|---|
| `mpc` (default) | everyday CPU / MPI work | 312 | 64 | 112 | 112 GiB | 24 h |
| `mpc_l` | longer CPU runs | 156 | 8 | 112 | 112 GiB | 72 h |
| `lmc` | very-large-memory jobs | 2 | 2 | 96 | 2880 GiB | 24 h |
| `gpu` | GPU batch work | 3 | 1 | 112 | 448 GiB | 72 h |
| `gpu_i` | interactive GPU sessions | 1 | 1 | 112 | 448 GiB | 24 h |

`mpc_l` is the first half of the `mpc` nodes, not separate hardware. Each
project also has caps shared by all its users: 7168 running cores and 5000
submitted jobs on MPC, 192 cores and 100 jobs on LMC, 448 cores and 100 jobs on
GPU.

**A job that exceeds a partition limit is accepted but never runs.** It stays
pending (reason `PartitionTimeLimit` or `PartitionNodeLimit`) until someone
cancels it, so size jobs inside the table. A job may overrun its time limit by
up to 30 minutes before it is killed. Maintenance reservations cover whole
partitions; a job whose time limit reaches into one waits with reason
`ReqNodeNotAvail, Reserved for maintenance`.

### How a job is billed

Billing is the largest of the weighted resources a job requests: on `mpc` and
`mpc_l`, the larger of cores and GiB of memory; on `lmc`, the larger of cores
and GiB/30; on the GPU partitions, the largest of cores, GiB/4 and 28 per GPU.
So a single-core job asking for 112 GiB on `mpc` is billed as a whole node.
Nodes are shared between jobs core by core; a job has a node to itself only if
it asks for all its cores.

How many nodes each partition has and how busy it is right now both change over
time — let the agent read current state (`get_facility` / live status) and tell
you where a job will start soonest, rather than planning around remembered counts.
For a GPU job, request GPUs explicitly (one GPU also reserves 28 CPU cores,
14 on each socket, and 112 GiB, and is billed as 28 cores; a job is limited to
one GPU node); the agent handles the GPU-specific scheduling details.

## Storage

Four places to keep data, each with a purpose:

- `/home/<user>` — code, scripts, small files; ~4 TB quota.
- `/data/<projectID>` — larger datasets and shared project results; requested
  through the portal and charged, so it is opt-in per project.
- `/tmp_work` — shared scratch on its own Lustre file system, with no user
  quota; files are deleted once they have gone unmodified for a week, so copy
  anything you want to keep back to home or project storage. It is flat and
  world-writable: create `/tmp_work/<user>` and work inside it. **The GPU
  nodes do not mount `/tmp_work`**, so stage GPU jobs through home or the
  node's `/tmp`.
- node-local disk — `/tmp` on each compute node (`TMPDIR=/tmp` in jobs), about
  105 GB on MPC nodes and 339 GB on GPU nodes. It is shared with other jobs on
  the node and out of reach once the job ends, so use a job-specific
  subdirectory and copy results back before the job finishes.

Home has an inode quota of 10 million files; workflows that write many small
files hit it before the block quota, so archive per-step outputs.

The shared filesystem is Lustre; the agent can report your current usage and quota
on request.

## Software, compilers, and MPI

Software is provided through environment modules. Because versions change, have
the agent list what is installed live rather than relying on a frozen list — but a
few stable conventions matter when building and launching:

- **Intel oneAPI is the primary toolchain.** Loading the `intel` module (default
  25.3.0) brings in the Intel compilers and **Intel MPI** together; this is the
  recommended default for building and running MPI code on the Xeon nodes.
  25.3.0 provides only the LLVM compilers (`icx`, `icpx`, `ifx` and the
  `mpiicx`/`mpiifx` wrappers). The classic `icc`, `icpc` and `ifort` — which the
  official guide's examples still use — exist only under `intel/23.02.1`.
- **Launch MPI work with `srun`.** Under Slurm, `srun` inherits the job's
  allocation, so you don't pass process counts on the launch line — the resources
  you requested for the job determine them. Intel MPI works with plain `srun`.
- **Open MPI is available as an alternative** (`openmpi/4.1.6`, usually with
  `gcc/13.2.0`), but its module **conflicts with Intel MPI** — load one or the
  other, not both (unload/`module purge` first if switching). Launch Open MPI
  programs with `srun --mpi=pmix`.
- **Threaded code must be told its thread count** (match it to the cores you
  reserve per process), or it may run with the wrong number and far slower.
  HBW2 runs Slurm 23.02, where `srun` no longer inherits `--cpus-per-task` from
  the job, so a threaded or hybrid job must also repeat it on the launch line
  (`srun --cpus-per-task=<n>`); otherwise each process is pinned to one core.
- **Job shells need the same modules as the build.** A job that runs a binary
  built under `module load intel` must load it again, or it fails with
  `error while loading shared libraries`. Do not submit with `--export=NONE`:
  such a job cannot see the HOKUSAI module trees at all.

There are no modules for CP2K, libxc, libint, ELPA or PLUMED, and no Python or
conda module; build such dependencies in home, using MKL for BLAS, LAPACK,
ScaLAPACK and FFTW. Other modules include GCC 13.2, `gcc-toolset` 15.1, CMake,
FFTW, HDF5, NetCDF, Boost and CUDA 11.7 to 13.2.

Major preinstalled applications include Gaussian, GROMACS, AMBER, NAMD, GAMESS,
ADF, ROOT, VMD, and GaussView, plus OpenMX, AkaiKKR, SMASH and NBO7 — but
confirm availability and version live, since the catalogue evolves.

## Running work through the agent

You describe a job in resource terms — how many nodes, how many processes (MPI
ranks) per node, how many threads per process, how much memory and wall time, and
which project to charge — and the agent assembles and submits the batch job, then
returns an identifier to track. You do not write batch scripts or recall scheduler
flags yourself. Most jobs are pure-CPU MPI or a hybrid of MPI across nodes with
threads within each node.

One operational detail: compute nodes have no direct internet route. Outbound
connections are relayed through a proxy on the front end (the
`$SLURM_SUBMIT_HOST` on port 3128), which the agent sets up when a running job
needs to fetch something.

## Bringing your own environment with containers

Singularity is available when you want to carry a specific software stack onto the
machine. Pull a ready-made image from a public registry or build your own, then
have the agent run your program inside it; for GPU jobs the agent enables
container GPU access automatically.

## Following jobs and untangling failures

After submission, a job's queue position, state, and history come from the
scheduler — ask the agent and it reports back in plain language, including why a
waiting job is waiting. HBW2 orders jobs by fair-share priority alone: waiting
does not raise a job's priority and small jobs get no boost, so a project with
heavy recent usage can wait a long time even for a tiny job. A job's console
output is written to a file named `slurm-<jobid>.out` in the directory it was
launched from, which the agent can read and summarize.

When a job misbehaves, the cause is usually one of a few:

- No project was named, or the project's core-time is exhausted.
- It ran out of memory — move to a bigger memory share or the `lmc` partition.
- It hit its wall-time limit — raise the limit or use `mpc_l` for longer runs.
- Its thread count was left unset, or `srun` was not given `--cpus-per-task`,
  so performance collapsed.
- It asked for more than a partition allows, so it never started.
- A GPU job tried to use `/tmp_work`, which the GPU nodes do not mount.
- Conflicting MPI modules (Intel MPI and Open MPI) were loaded together.

The agent can inspect the failed job's record and output to point at which it was.

## Staying current

HBW2 evolves — partitions, installed software, limits, and policies change. The
authoritative sources are the portal and the live state of the machine, which the
agent can query whenever a precise, current answer matters. For anything not
covered here — especially accounts, allocations, and policy — fall back to those
or to RIKEN R-CCS support.
