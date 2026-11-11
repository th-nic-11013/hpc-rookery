## HOKUSAI notes

- **GPUs:** partition `gpu` (bwgpu1-3, 4 x H100 each, one node per job,
  72 h); `gpu_i` (bwgpu4, 24 h) for short tests. The queue is usually
  full: a 1-GPU, 5-minute job has waited 17 h. Plan for hours of waiting.
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
  | `mace-matpes-r2scan-0` | r2SCAN (MatPES), no dispersion | `mace-matpes-r2scan-0/MACE-matpes-r2scan-omat-ft.model` | `matpes-r2scan-replay-data.extxyz` (1.18 GB, mace_matpes_0 release): **not downloaded yet** |
  | `mace-mp-0-medium` | PBE / PBE+U (Materials Project) | `mace-mp-0-medium/2023-12-03-mace-128-L1_epoch-199.model` | `replay={"selected": "mp"}` lets MACE fetch its own |

- **Run here so far:** one 2-epoch, 1-GPU test (job 9765431, 2026-11-11,
  44 s) with the launch line `launch_plan` gives. Not yet run: more than
  one GPU, replay selection, a multihead run.
- **Precision:** `mace-matpes-r2scan-0` and `mace-mp-0-medium` are
  float64 models. Pass `foundation.dtype="float64"`: a float32 run on
  them stops at the first evaluation ("both inputs should have same
  dtype").
