# Facilities onboarded so far

Generated tables between the markers (`scripts/render_facility_tables.py`); moved out of AGENTS.md on 2026-11-11.


<!-- FACILITY_TABLE:START -->
### Live-tested facilities

These integrations have been exercised against their real scheduler.

| slug | facility | scheduler | description |
|---|---|---|---|
| `crux` | Crux (ALCF) | pbs | Argonne ALCF Crux: 256-node HPE Cray EX AMD Rome CPU system, PBS Professional with sbank accounting, mandatory project and filesystems directives; reached over a multiplexed SSH connection the user authenticates once with a one-time passcode. |
| `fugaku` | Fugaku | pjm | 158,976-node A64FX (Arm SVE) system, Fujitsu PJM scheduler, no GPUs; a project group is mandatory on every job. |
| `hokusai` | HOKUSAI BigWaterfall2 (HBW2) | slurm | CPU-first Slurm cluster (312-node MPC, large-memory, H100 GPU subsystems); a project account is mandatory on every job. |
| `miyabi` | Miyabi (JCAHPC) | pbs | JCAHPC CPU/GH200 system with PBS Professional, full-GPU and MIG queues; reached over a multiplexed SSH connection the user authenticates once with a one-time code. |
| `rccs-cloud` | R-CCS Cloud | slurm | Heterogeneous ~20-partition cluster (CPU/NVIDIA/AMD/Intel GPU), Slurm with accounting. |
| `rikyu` | RIKYU (RIKEN AI4S / GB200) | slurm | RIKEN AI4S GB200 GPU cluster, Slurm with accounting, job-total GPU request. |

### Awaiting live validation

These ports register and pass the offline test suite, but have not yet
completed an end-to-end check against the current live system.

| slug | facility | scheduler | description |
|---|---|---|---|
| `cell2026` | cell2026 (Shinobu Lab) | gridengine + slurm | Dual-scheduler GPU cluster: Grid Engine on helix/kinase with managed RTX A4000s and durable qacct, plus no-accounting Slurm on beta/serine with unmanaged RTX 4000 Ada/RTX 5090 GPUs. |
| `irene` | Irene (CEA TGCC) | bridge | CEA TGCC CPU-first system with AMD Rome, large-memory, and NVIDIA GPU partitions, accessed through the Bridge #MSUB/ccc_* scheduler interface. |
| `octopus` | Octopus (RIKEN R-CCS) | slurm | Four-node, dual-vendor GPU cluster: three NVIDIA H200 nodes and one AMD MI300X node, Slurm accounting, and vendor-specific container passthrough. |
| `tsubame` | TSUBAME4.0 (Science Tokyo) | gridengine | Science Tokyo's GPU-first H100 system, scheduled by Altair Grid Engine using fixed resource-type slices and TSUBAME group points. |
<!-- FACILITY_TABLE:END -->

