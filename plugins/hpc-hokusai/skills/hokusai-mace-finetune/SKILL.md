---
name: hokusai-mace-finetune
description: Fine-tune a MACE foundation model on HOKUSAI BigWaterfall2 (HBW2) - check the labelled data, write the run folder, select replay data, train on GPUs and read the log. Use whenever the user wants to fine-tune, retrain or train MACE (multihead replay, naive, LoRA or from scratch), continue a training run that hit the time limit, or asks how a training run went - even if they only say "train the model on these labels".
---

# MACE fine-tuning on HOKUSAI BigWaterfall2 (HBW2)

Every cluster tool call uses `facility="hokusai"`. Show the user the
warnings of each tool, the job spec and the GPU cost, and wait for
permission before each submit.

## Contract

- **Reads:** `software_registry` (the `mace` build) and the pack's model
  table, through `launch_plan` and `records_query`.
- **Writes:** a run folder under `~/agent/work/mace-finetune/<name>/` on the
  cluster: the split data, `train.sh`, `select_replay.sh`, `e0s.json`,
  `manifest.json`, then `checkpoints/`, `logs/`, `results/` and the model.
- **Tools:** `mlip_check_dataset`, `mace_make_finetune`, `mace_parse_log`,
  and for CP2K E0s `cp2k_make_subsys`, `cp2k_make_input`, `cp2k_make_e0s`
  (the hyogo server; all local), `labels_compare` (yagura; local),
  `launch_plan` (GPU job spec),
  `fs_upload` (takes a folder), `submit_job`, `get_job_status`,
  `fs_tail`, `fs_download`.
- **Preconditions:** a labelled extxyz on this machine (CP2K labels:
  `cp2k_make_labels`), E0s for its elements, and the user's approval.
- **Done when:** `mace_parse_log` says finished and evaluated, and the
  errors per head are reported with the model's path; or the failure is
  reported with no number quoted.

## 1. Check the data

```text
mlip_check_dataset(paths=["labels.extxyz"])
```

Stop on `ok: false` and read the problems to the user. `d3` says whether
the labels include dispersion: a model trained on D3-free labels gets D3
added when it runs (`lammps_make_input`'s `dispersion`), a model trained
on labels with D3 does not. Never both.

When some frames were labelled twice (a relabel at other settings, another
starting MAGMOM, +U), compare the two sets before choosing:

```text
labels_compare(a_path="labels.extxyz", b_path="relabel.extxyz")
```

A flagged frame sits in another state in one of the sets (spin state, SCF
solution): its force difference, its distance from the median energy
shift or a moment that changed sign says why. Show the user the flagged
frames; drop or relabel them before training, never average the two.

## 2. E0s at the labels' level of theory

MACE subtracts one energy per element (E0) before it learns. They must
come from the labelling code with the labels' exact settings: a
foundation model's own E0s are on its training code's energy zero, and
`"average"` is a fit, not an atom. With CP2K labels, per element:

```text
# one atom in a >= 15 A cubic box, written by hand as a 3-line extxyz
cp2k_make_subsys(xyz_path="<El>-15/atom.xyz", kinds=<labels' cp2k_kinds>, periodic="NONE", ...)
cp2k_make_input("sp", <labels' functional>, <that subsys>, <labels' kinds>,
                purpose="atom", multiplicity=<m>, cutoff_ry=<labels'>, rel_cutoff_ry=<labels'>)
```

Run the free atom's ground-state multiplicity (H 2, C 3, N 4, O 3, Zr 3)
and at least one other, and repeat the heaviest elements' lowest state in
a 20 A box. `cp2k_check_input` each, run them in one CPU job (each is a
few minutes), `fs_download` the `.out` files, then:

```text
cp2k_make_e0s(runs=[{"out": ..., "inp": ..., "subsys": ...}, ...],
              out_path="<local>/e0s.json",
              label={"inp": <a label run's .inp>, "subsys": <its subsys.inc>, "out": <its .out>})
```

It refuses atoms whose settings differ from each other or from the
labels, and keeps the lowest multiplicity per element. Read the warnings
to the user (ground state not reached, boxes differing by more than
1 meV). Its `out` is the `e0s` path for step 4.

## 3. Choose with the user

| choice | default | notes |
|---|---|---|
| method | `multihead` | replay keeps the foundation model's behaviour; `naive` forgets it; `scratch` is the baseline; `lora` needs a newer mace-torch than some clusters have (the tool refuses) |
| foundation model | ask | from the model table below; its level of theory should match the labels |
| E0s | **must be stated** | step 2's `e0s.json`, or `{Z: eV}` computed the same way. `"foundation"` and `"average"` are stated shortcuts; both get a warning, and with labels from another code than the foundation model's they leave an energy offset of eV/atom |
| replay | pool + 10,000 samples + keys | multihead only; the pool is the foundation model's replay data on the cluster; `keys` are its label names from the model table (`replay.keys`; MATPES: `dft_*`, whose plain `energy`/`stress` are a model's prediction); `stress_sign` -1 when the table says the file's stress has the opposite sign (MATPES): `train.sh` then trains the replay head on a flipped copy |
| split | 10 % validation, per `config_type` | `test_fraction`, or `holdout=[config_types]` for a test set the model never sees |
| GPUs | 1 | each GPU is billed as 28 cores; `batch_size` is per process, so more GPUs means a larger effective batch |
| device, precision, batch | GPU, float32, largest batch that fits | see "Making training fast" below; CPU nodes are a real option when the GPU queue is long |

Models installed on HOKUSAI BigWaterfall2 (HBW2) and what each was trained on are in
the facility notes at the end. The mace-torch version is the `mace`
build's `version` in `software_registry`.

## 4. Write the run folder

```text
plan = launch_plan(facility="hokusai", code="mace", run_type="train",
                   system={"atoms": <atoms per frame>}, gpus=1)
mace_make_finetune(paths=["labels.extxyz"], out_dir="<local>/<name>", name="<name>",
                   mace_version=plan["build"]["version"], e0s=<{Z: eV} | path>,
                   method="multihead",
                   foundation={"model": "<model path on the cluster>", "dtype": "<its dtype>"},
                   replay={"pool": "<replay data on the cluster>", "num_samples": 10000,
                           "keys": <the model table's replay.keys>,
                           "stress_sign": <the model table's replay.stress_sign, if any>},
                   distributed=plan["layout"]["distributed"])
```

**On CPU nodes instead:** `launch_plan(..., device="cpu")` gives the
facility's measured CPU layout (threads bound to cores; it refuses where
none is measured), and `mace_make_finetune(..., overrides={"device":
"cpu"})` writes both `train.sh` and `select_replay.sh` for the CPU. Use
the CPU plan's `job_spec` in steps 5 and 6. See "Making training fast".

Nothing runs. Read `warnings` to the user: every value that is not from
a finished reference run is named there. Then
`fs_upload(local_path="<local>/<name>", remote_path="~/agent/work/mace-finetune/<name>")`.

## 5. Select the replay data (multihead only)

One GPU, usually minutes to an hour:

```text
submit_job(spec = launch_plan(..., gpus=1)["job_spec"] + {
    "name": "<name>-select", "directory": "~/agent/work/mace-finetune/<name>",
    "executable": "bash select_replay.sh",
    "attributes": {"duration": "02:00:00"}})
```

It must leave `replay/selected_replay.xyz`. Skip this step when
`replay={"selected": ...}` named a file that already exists.

## 6. Train

```text
submit_job(spec = plan["job_spec"] + {
    "name": "<name>", "directory": "~/agent/work/mace-finetune/<name>",
    "executable": "bash train.sh",
    "attributes": {"duration": "<walltime>"}})
```

`launch_plan` has no timing for GPU runs, so the walltime is a choice:
say so, and prefer a short first job. While it runs,
`fs_tail` the Slurm log or `logs/<name>_run-<seed>.log`.

**If the job hits the time limit:** `mace_make_finetune(..., restart=true,
overwrite=true)` writes the same run with `--restart_latest`; upload only
`train.sh` and submit again. It refuses to start without a checkpoint.

## Making training fast

Time per epoch is `epoch_wall_s` from `mace_parse_log`. Measure before a
long run: a 2-epoch copy of the run folder (`overrides={"max_num_epochs":
2}`) costs minutes and tells you the per-epoch time, memory and that the
setup loads. Compare only like with like (same data, replay file, dtype
and batch size), and check a baseline's setup before quoting a speed-up
against it: one unbound CPU run here looked 7x slower than the hardware is.

- **Precision:** train in float32 from a float32 copy of the foundation
  model (`model.float()`, saved next to it; pass `foundation.dtype
  "float32"`). It halves memory, is ~1.2-1.3x faster, and reached the
  same errors as float64 after 20 epochs. A float64 model with
  `--default_dtype float32` stops at the first evaluation.
- **Batch size:** the largest that fits. Small frames leave the GPU
  waiting on per-step overhead, so batch 2 was 1.4x faster than batch 1
  and its replay head was calmer. On CPU a larger batch helps too (batch
  16 was 1.7x faster per epoch than batch 2, similar errors after 3 epochs). Memory grows about linearly with batch
  size and with atoms per frame; a GPU out-of-memory error says the batch
  is too big. A larger batch means fewer optimizer steps per epoch, so
  compare accuracy after the same number of steps, not epochs.
- **GPU memory:** set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`.
  `nvidia-smi` shows what PyTorch reserved, not what it needs; the live
  peak is `torch.cuda.max_memory_allocated`.
- **CPU training** (`--device cpu`, same errors as GPU, epoch for epoch).
  Set up well, a CPU node came within 1.5x (P100) to 6.5x (H100) of a
  GPU; set up badly it was 10x slower than that. `launch_plan(...,
  device="cpu")` writes the setup below into the job spec.
  - **Bind the threads to cores** (`OMP_PROC_BIND=close OMP_PLACES=cores`
    and `srun --cpu-bind=cores`) and keep one run inside **one socket**.
    Unbound threads on a shared node, or one run spread over both
    sockets, were 4-10x slower.
  - **Pick the thread count by measuring, in steps of the node's NUMA
    domains** (`lscpu | grep -i numa`), **at the batch size you will
    train with**. The best count moves with the batch: on a node with
    14-core domains, 28 threads were fastest at batch 1, but 56 beat 28 by
    1.3x at batch 16. Run 2-epoch copies at 1, 2 and 4 domains on a node
    of their own and keep the fastest; record it in the facility's
    `MACE_CPU` value.
  - **The node to yourself** (`exclusive_node_use`), or neighbours'
    jobs slow yours; record the binding (`cpuinfo.txt` from `lscpu` and
    `taskset -cp $$`, or `OMP_DISPLAY_AFFINITY=TRUE`).
  - **Several runs per node** (committee members, a scan): one `srun
    --exact -n1 -c<threads> --mem=<share> --cpu-bind=cores` step each,
    then `wait`. Two runs, one per socket, each kept full speed.
  - **Memory:** at least 1.5x the measured peak per run (`sacct
    MaxRSS`); it grows with the batch (12 GB at batch 1, 36 GB at batch
    16 for 456-atom frames), so a default of 1 GB per core is too little.
  - **Count the queue wait:** a short CPU job that starts now can finish
    before a GPU job that waits hours.

The facility notes below give the measured numbers here.

## 7. Read the result

```text
fs_download(remote_path="~/agent/work/mace-finetune/<name>/logs/<name>_run-<seed>.log", ...)
mace_parse_log(log_path=...)
```

Report, per head: the error before training (`initial`), at the saved
checkpoint (`at_best_epoch`), and the final error table. With two heads,
`Default` is the fit to the new labels and `pt_head` is what was kept of
the foundation model. Read every warning: ERROR lines while loading data,
and an initial energy error of hundreds of meV/atom, which means the E0s
are on a different energy zero than the labels.

The model is `checkpoints/<name>_run-<seed>.model`. To run it in LAMMPS
it has to be exported first; that step is not a tool yet.

## Picking the frames to label (before labelling)

From an MD pool (extxyz, e.g. `structure_to_extxyz` on LAMMPS dumps),
`mlip_select_frames` (hyogo, local) picks the frames to label. First
without quotas: it returns the strata (stratum key x volume bin), how
many frames each holds and how many fit at `min_spacing_ps`; agree the
quotas with the user, then call again with `quotas` and `out_dir`.
Hold out whole time blocks (`holdout=[{"source": "A3-*", "last_ps": 50}]`),
never random frames: frames 25 fs apart are near copies. Round 2:
`exclude=[<round-1 manifest.json>, <labels extxyz>]`, and `scores=` a
per-frame disagreement array in place of farthest-point sampling.

Without descriptors it spreads picks by volume alone. Descriptors
(per-element means of the base model's invariant features) come from
`scripts/mace_descriptors.py` on the cluster, in the MACE environment,
on the same pool files: `fs_upload` the script and the pool, then

```text
submit_job(spec = launch_plan(facility="hokusai", code="mace", run_type="train",
                              system={"atoms": <atoms per frame>}, gpus=1)["job_spec"] + {
    "name": "<name>-desc", "directory": "<pool folder>",
    "executable": "python mace_descriptors.py --model <base model> --out desc.npy <pool files>",
    "attributes": {"duration": "<walltime>"}})
```

`--dry-run` counts and hashes the pool without a model. `fs_download`
`desc.npy` and `desc.json` and pass `descriptors="desc.npy"`; the tool
refuses descriptors computed on other files.

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
