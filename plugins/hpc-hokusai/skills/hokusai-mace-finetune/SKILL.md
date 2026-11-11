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
                purpose="atom", multiplicity=<m>, cutoff=<labels'>, rel_cutoff=<labels'>)
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
| replay | pool + 10,000 samples | multihead only; the pool is the foundation model's replay data on the cluster |
| split | 10 % validation, per `config_type` | `test_fraction`, or `holdout=[config_types]` for a test set the model never sees |
| GPUs | 1 | each GPU is billed as 28 cores; `batch_size` is per process, so more GPUs means a larger effective batch |

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
                   replay={"pool": "<replay data on the cluster>", "num_samples": 10000},
                   distributed=plan["layout"]["distributed"])
```

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
- **E0s:** with CP2K labels (GTH pseudopotentials), the models' own
  VASP/PAW E0s and `"average"` both leave the energy about 5.6 eV/atom off
  (job 9765431). Compute them with step 2 on `mpc`; no E0 set has been
  run here yet.
