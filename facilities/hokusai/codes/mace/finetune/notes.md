## HOKUSAI notes

- **GPUs:** partition `gpu` (bwgpu1-3, 4 x H100 each, one node per job,
  72 h); `gpu_i` (bwgpu4, 24 h) for short tests. The queue is usually
  full: a 1-GPU, 5-minute job has waited 17 h. Plan for hours of waiting.
- **Job spec:** put resources under `resources` and the partition and
  time under `attributes` (`queue_name`, `duration`); flat keys such as
  `nodes` or `memory` are refused, and the error says where each goes.
  `memory` takes bytes or a string such as `"48G"`. Check the rendered
  script before submitting.
- **Budget:** GPU hours come from the project's `gpu` budget
  (`get_project_allocations`), separate from `mpc`. One GPU-hour is
  billed as 28 core-hours.
- **Storage:** GPU nodes do not mount `/tmp_work`; keep runs under
  `~/agent/work/`.
- **Software:** mace-torch 0.3.14 in `~/software/mlip/2026.10` (no LoRA
  in that version). The torch wheel carries its own CUDA; no cuda module
  is needed.
- **Models on the cluster** (`~/software/mlip/2026.10/models/`):

  | name | trained on | file | replay data |
  |---|---|---|---|
  | `mace-matpes-r2scan-0` | r2SCAN (MatPES), no dispersion | `mace-matpes-r2scan-0/MACE-matpes-r2scan-omat-ft.model` (float32 copy: `...-omat-ft-f32.model`) | `mace-matpes-r2scan-0/matpes-r2scan-replay-data.extxyz` (1.18 GB, mace_matpes_0 release) |
  | `mace-mp-0-medium` | PBE / PBE+U (Materials Project) | `mace-mp-0-medium/2023-12-03-mace-128-L1_epoch-199.model` | `replay={"selected": "mp"}` lets MACE fetch its own |

- **Run here so far:** multihead replay fine-tunes of `mace-matpes-r2scan-0`
  on UiO-66 (456 atoms): replay selection on one GPU, four 100-epoch
  members (jobs 9864324-27, 2026-11-23) at about 1 h 23 min each on one
  H100. Not yet run: more than one GPU.
- **Precision:** the foundation models are float64. Train from the float32
  copy (`foundation.dtype="float32"`): a float64 model with float32
  training stops at the first evaluation ("both inputs should have same
  dtype").
- **Speed, one H100** (`gpu`): UiO-66 multihead, float32, batch 2, 235 +
  1,002 replay training frames: 47.1 s/epoch. The 2-epoch float64 test
  (104 UiO-66 + 180 replay frames, batch 1): 23.5 s/epoch (job 9825931).
- **CPU training on `mpc`** (2 sockets x 56 cores, Xeon Max 9480, SNC4:
  8 NUMA domains of 14 cores, 112 GiB). `launch_plan(..., device="cpu")`
  gives the measured layout (`MACE_CPU`: 56 threads bound, node to itself,
  100 GB), chosen for large batches. The 2-epoch float64 test (batch 1),
  node to itself, threads bound, identical errors in every run (cpu-scan
  2026-11-25):

  | threads | s/epoch | job |
  |---|---|---|
  | 14 (1 domain) | 230 | 9965819 |
  | **28 (2 domains)** | **154** | 9965820 |
  | 56 (1 socket; two copies, one per socket) | 217 / 225 | 9965512 |
  | 112, unbound | 817 | 9965511 |
  | 56, unbound, shared node | 1645 | 9819992 |

  The P100 is 1.5x and the H100 6.5x faster than the best CPU layout.
  Round-2 settings (float32, 235 + 1,002 replay frames), 56 threads bound:
  batch 2 217, batch 4 180, batch 8 146, batch 16 128 s/epoch (jobs
  9965821, 9970021-23) vs the H100's 47.1 at batch 2. Batch 16 with 28
  threads: 167 s/epoch (job 9976295, same errors), so at large batches 56
  threads win. Peak memory: 12.3 GiB for the batch-1 test, 35.8 GB at
  batch 16 (28 threads). The whole node is billed while you hold it alone; two runs
  per node (one `srun --exact` step each) each kept full speed. With the
  GPU queue's waits (hours), `mpc` can be the faster route for short runs.
- **E0s:** with CP2K labels (GTH pseudopotentials), the models' own
  VASP/PAW E0s and `"average"` both leave the energy about 5.6 eV/atom off
  (job 9765431). Compute them with step 2 on `mpc`; no E0 set has been
  run here yet.
