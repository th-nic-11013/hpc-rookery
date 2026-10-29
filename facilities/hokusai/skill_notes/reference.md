HBW2 is a **CPU-first** RIKEN R-CCS supercomputer, Slurm-scheduled,
reached via shared login nodes `hokusai1`–`hokusai4` (`hokusai.riken.jp`,
DNS round-robin, so two logins can land on different nodes).

### Subsystems

| subsystem | nodes | cores/node | usable mem/node | node names |
|---|---|---|---|---|
| **MPC** — the workhorse | 312 | 112 (2 × 56, Xeon Max 9480) | 112 GiB | `bwmpc001`–`bwmpc312` |
| **LMC** — large memory | 2 | 96 (Xeon Gold 6418H) | 2880 GiB | `bwlmc1`–`bwlmc2` |
| **GPU** — mainly postprocessing, secondary | 4 | 112 (2 × 56, Xeon Platinum 8480+) + 4 × H100 | 448 GiB | `bwgpu1`–`bwgpu4` (`bwgpu4` = `gpu_i`) |

All x86-64 (Sapphire Rapids generation), InfiniBand NDR, one shared Lustre
file system. Compute nodes have hyper-threading off. MPC runs Rocky Linux 8;
LMC, GPU and the front end run RHEL 8.

### Partitions

| partition | nodes | max nodes/job | max wall | default mem/core |
|---|---|---|---|---|
| `mpc` (default) | `bwmpc[001-312]` | 64 | 24 h | 1 GiB |
| `mpc_l` | `bwmpc[001-156]` | 8 | 72 h | 1 GiB |
| `lmc` | `bwlmc[1-2]` | 2 | 24 h | 30 GiB |
| `gpu` | `bwgpu[1-3]` | 1 | 72 h | 4 GiB |
| `gpu_i` | `bwgpu4` | 1 | 24 h | 4 GiB |

Default wall time 1 h. Per-project caps: MPC 7168 running cores / 5000 jobs,
LMC 192 / 100, GPU 448 / 100. Jobs over a partition limit are accepted and
**never start** (`EnforcePartLimits=NO`). `OverTimeLimit=30` minutes.
Array indices up to 1000 (`MaxArraySize=1001`).

**Billing = max of weighted resources**: on `mpc` max(cores, GiB), on `lmc`
max(cores, GiB/30), on GPU max(cores, GiB/4, 28 × GPUs). Nodes are shared
core by core between jobs.

### Storage

| area | path | quota | lifetime |
|---|---|---|---|
| home | `/home/<user>` (really `/lustre/home/<user>`) | 4 TB, 10 M inodes | permanent |
| project data | `/data/<projectID>` | from 1 TB, 600k inodes/TB; opt-in, charged | project lifetime |
| shared scratch | `/tmp_work` (separate ~930 TB Lustre) | none | files deleted once unmodified for a week |
| node-local | `/tmp` (`TMPDIR=/tmp`), ~105 GB MPC, ~339 GB GPU | — | job duration; shared with other jobs on the node |

`/tmp_work` is flat and world-writable: create `/tmp_work/<user>`. **GPU
nodes do not mount `/tmp_work`.** Many projects have no `/data` area.
Usage: `lfs quota -h -p ${UID} ${HOME}`; for a data area
`lfs quota -h -p <GID> /data/<project>`, where the GID is that of the `RB…`
group, or the `HP…` group's GID with a `1` prepended. Core-time budget per
subsystem and expiry: `listcpu -p <project>` (lists `mpc`, `lmc`, `gpu`).

### Software

Environment modules. **Intel oneAPI is primary**: `module load intel`
(default 25.3.0) → Intel compilers + Intel MPI. 25.3.0 has only the LLVM
compilers `icx`/`icpx`/`ifx`; the classic `icc`/`icpc`/`ifort` exist only
under `intel/23.02.1`. Open MPI (`openmpi/4.1.6`, with `gcc/13.2.0`) is an
alternative that **conflicts** with Intel MPI — load one only. Launch with
`srun` (Intel MPI) or `srun --mpi=pmix` (Open MPI), repeating
`--cpus-per-task` for threaded jobs (Slurm 23.02). Also: `gcc-toolset/15.1.1`,
`cmake/3.30.1`, FFTW 3.3.10, HDF5, NetCDF, boost, CUDA 11.7–13.2. Singularity
(singularity-ce 3.11) for containers. Major apps: Gaussian, GROMACS, AMBER,
NAMD, GAMESS, ADF, ROOT, VMD, GaussView, plus OpenMX, AkaiKKR, SMASH, NBO7 —
confirm versions with `module avail` live. **No CP2K, libxc, libint, ELPA or
PLUMED modules**: build those under home against MKL.

There is **no python/conda module**: the system Python is bare 3.6 with
no third-party packages. Compute nodes have no direct internet route, so
bring pure-Python dependencies along (upload and use via `PYTHONPATH`),
or install into home on the login node first.

### Login-node limits

Each user is limited to **256 processes and 48 GiB of virtual memory** on a
front-end node. Light edits, small compiles and queries only; `make -j`
beyond a few jobs, test suites and post-processing belong in a job. Don't
run recursive scans (`find`, `du`, `ls -R`) over `/lustre`, `/home`,
`/data` or `/tmp_work` as a whole — Lustre metadata storms slow the machine
for everyone. Scan only inside your own directories.

### GPU dialect

Request with a job-total GPU count (`resources.gpus`, max 4, one node);
one GPU reserves 28 CPU cores (14 per socket) and 112 GiB, and is billed as
28 cores; container flag `--nv` (single vendor, NVIDIA H100). GRES name is
`gpu:hopper`.

### Accounting

Every job is billed to a project (`--account`); RIKEN IDs start `RB`, HPCI
`HP`; the Slurm account name is the lowercase form. Fair share is the only
non-zero priority weight, decaying with a 30-day half-life. Read standing
live with `get_projects(facility="{{SLUG}}")`.

### Guide errata

The official User's Guide (v1.14) has typos that fail if copied: `#PJM` →
`#SBATCH`; `--bigin` → `--begin`; `--no-request` → `--no-requeue`;
`--fomat` → `--format`; `-- gpu=` → `--gpus=`; `Gpu_i` → `gpu_i`; its
`sinfo` example (1–12 nodes) is stale (live: 64 for `mpc`, 8 for `mpc_l`);
its compile examples use the classic `ifort`/`icc`/`mpiifort`/`mpiicc`,
absent from the default `intel/25.3.0`.
