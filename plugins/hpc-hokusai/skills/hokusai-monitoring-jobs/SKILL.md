---
name: hokusai-monitoring-jobs
description: Use when the user asks about the status, progress, output, history, or failure of jobs on HOKUSAI BigWaterfall2 (HBW2), or about queue and node availability. Also use this any time you (the agent) need to check on or wait for a job you submitted, even without a fresh user request.
user-invocable: true
---

# Monitoring jobs on HOKUSAI BigWaterfall2 (HBW2)

Every tool call in this skill uses `facility="hokusai"`.

## Status checks (same tools and semantics everywhere)

Use these tools even when you're checking in on your own initiative — don't
fall back to `run_command_on_cluster` with raw `squeue`/`sacct` just
because there's no new user message prompting it; the tools below return
the same information normalized, plus any facility-specific lag/quirk
handling noted below.

- **One job**: `get_job_status(facility="hokusai", job_id=...)` — `state`
  is normalized (`queued`/`active`/`completed`/`failed`/`canceled`/`held`/
  `unknown`); `meta_data.native_state` is the scheduler's own. A queued
  job's `message` explains the wait.
- **My recent jobs**: `get_job_statuses(facility="hokusai", job_ids=[])`
  for roughly the last two days, or pass specific IDs.
- **Cluster availability**: `get_resources(facility="hokusai")` —
  per-partition allocated/idle/other/total node counts. Idle nodes can
  start jobs immediately.
- **Reading a job's output**: the file is `slurm-<job_id>.out` inside the
  directory the job ran in. **Get that directory from the job's own status
  record** (`meta_data.workdir`) rather than assuming `$HOME` — a job whose
  spec set `directory` writes its output there, not in the home directory.
  Then read it with `fs_tail(facility="hokusai", path=...)` for a running
  job, or `fs_view` once it has finished.

## Fair-share is why a job waits

HBW2 orders jobs by **fair-share priority alone**: the multifactor weights
are fair share 400 and age, job size, partition and QOS all 0, with a 30-day
decay half-life. Waiting does not raise a job's priority and small jobs get
no boost, so a job can sit queued because your project's recent usage is
high, not because the cluster is full. When a job is waiting, check both:

- `get_projects(facility="hokusai")` — your fair-share standing and raw
  usage per account (read live; it moves continuously).
- `get_resources(facility="hokusai")` — live partition occupancy.

How long a small job waits depends on that standing. A project with good
fair share often starts short jobs at once through backfill; a project
with a low factor (≈ 0.1) has seen a 1-core, 5-minute `mpc` job wait 30
minutes and a 1-GPU `gpu_i` job wait 17.5 hours. Short, accurate
`duration` values help a job backfill.

## Reading the pending reason

| reason | meaning | action |
|---|---|---|
| `Priority`, `Resources` | normal waiting | wait |
| `PartitionTimeLimit`, `PartitionNodeLimit` | the job exceeds a partition limit; HBW2 accepts such jobs but **never runs them** | ask the user, cancel, resubmit within limits |
| `ReqNodeNotAvail, Reserved for maintenance` | its time limit overlaps a maintenance reservation | wait for maintenance, or resubmit with a shorter `duration`; `scontrol show reservation` shows the window |
| `Dependency` / `DependencyNeverSatisfied` | waiting on another job; dependents of a failed `afterok` job are **not** auto-cancelled here | cancel them (with the user's OK) |

## HBW2 failure modes and triage

- **No project / core-time exhausted** — check
  `get_projects(facility="hokusai")`; a project's new jobs stop starting
  once its allowance is spent.
- **Out of memory** (`native_state` OUT_OF_MEMORY) — raise the memory
  share, or move to the `lmc` partition (2880 GiB nodes). Remember memory
  above 1 GiB/core on `mpc` is billed as extra cores.
- **Hit wall time** (`native_state` TIMEOUT) — raise `duration`, or move
  from `mpc` (24 h) to `mpc_l` (72 h, max 8 nodes). The job actually ran up
  to 30 minutes past its limit (`OverTimeLimit=30`).
- **Threads unset or unbound** — not an error, a silent performance
  collapse. Two causes: `OMP_NUM_THREADS` not set to
  `resources.cpu_cores_per_process`, or a bare `launcher="srun"` on a
  threaded job (Slurm 23.02 does not pass `--cpus-per-task` to `srun`, so
  every rank's threads share one core). Use
  `launcher="srun --cpus-per-task=<n>"`.
- **Open MPI program misbehaves at startup** (the usual symptom of a PMI
  mismatch is N independent copies of rank 0 or a hang in `MPI_Init`) —
  check that the launcher is `srun --mpi=pmix`, the only form verified here
  for Open MPI.
- **Conflicting MPI modules** — Intel MPI and Open MPI loaded together.
  Load one only; `module purge` before switching.
- **`module: command not found` or modules missing in the job** — the job
  was submitted with `--export=NONE`; drop it.
- **Exit 127, `error while loading shared libraries`** — the run job is
  missing a `module load` that the build used (MKL, Intel MPI); job shells
  start with a clean environment, so repeat the module loads at runtime.
- **Classic compiler not found** (`ifort`/`icc`) — the default
  `intel/25.3.0` has only `ifx`/`icx`; use `intel/23.02.1` or the LLVM names.
- **`No such file or directory` under `/tmp_work` on a GPU job** — GPU nodes
  do not mount `/tmp_work`; stage under home.

Read the job's output and its `status.meta_data.native_state` together to
tell which of these it is. The exact submitted script is kept in
`~/agent/jobs/` — `fs_view(facility="hokusai", path=...)` it when
debugging.

## Checking what a job cost

`sacct -X -j <jobid> --format=JobID,Partition,Elapsed,AllocTRES%60,State`
(via `run_command`) shows `billing=` beside `cpu=`, `mem=` and `gres/gpu=`.
`billing` is the number of cores charged; if it is larger than `cpu`, the
memory or GPU request set the price.

## Polling politely

The login nodes are shared by hundreds of users. Poll `get_job_status` at
most about once a minute, and prefer a single check when you expect the job
to have finished.

## Cancelling and updating

- `cancel_job(facility="hokusai", job_id=...)` — **confirm with the user
  before calling this.**
- `update_job(facility="hokusai", job_id=..., spec={...})` — pass a
  JobSpec containing only what should change, e.g.
  `{"attributes": {"duration": "02:00:00"}}`. Only fields a scheduler can
  change after submission are applied (job name, wall time, partition,
  account, reservation, node count); anything else you set is reported back
  as not applied. Only affects jobs still queued or running.

## Polling until done, from a notebook

Interactively, just call `get_job_status` again after a pause. If you're
writing a reproducible notebook instead (see `hokusai-reproducing`), use
`hpc_agent_core.client`'s `wait_for_job(job_id, facility="hokusai")`
rather than a hand-rolled polling loop.

Don't guess facility-specific failure modes beyond what's noted above — use
`search_docs(facility="hokusai", query=...)` on the `hpc-docs` server.
