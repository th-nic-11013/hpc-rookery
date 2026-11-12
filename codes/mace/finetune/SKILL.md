---
name: {{SLUG}}-mace-finetune
description: Fine-tune a MACE foundation model on {{DISPLAY_NAME}} - check the labelled data, write the run folder, select replay data, train on GPUs and read the log. Use whenever the user wants to fine-tune, retrain or train MACE (multihead replay, naive, LoRA or from scratch), continue a training run that hit the time limit, or asks how a training run went - even if they only say "train the model on these labels".
---

# MACE fine-tuning on {{DISPLAY_NAME}}

Every cluster tool call uses `facility="{{SLUG}}"`. Show the user the
warnings of each tool, the job spec and the GPU cost, and wait for
permission before each submit.

## Contract

- **Reads:** `software_registry` (the `mace` build) and the pack's model
  table, through `launch_plan` and `records_query`.
- **Writes:** a run folder under `{{MACE_WORK_DIR}}/<name>/` on the
  cluster: the split data, `train.sh`, `select_replay.sh`, `e0s.json`,
  `manifest.json`, then `checkpoints/`, `logs/`, `results/` and the model.
- **Tools:** `mlip_check_dataset`, `mace_make_finetune`, `mace_parse_log`,
  and for CP2K E0s `cp2k_make_subsys`, `cp2k_make_input`, `cp2k_make_e0s`
  (the hyogo server; all local), `launch_plan` (GPU job spec),
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
| GPUs | 1 | each GPU is billed as {{MACE_CORES_PER_GPU}} cores; `batch_size` is per process, so more GPUs means a larger effective batch |

Models installed on {{DISPLAY_NAME}} and what each was trained on are in
the facility notes at the end. The mace-torch version is the `mace`
build's `version` in `software_registry`.

## 4. Write the run folder

```text
plan = launch_plan(facility="{{SLUG}}", code="mace", run_type="train",
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
`fs_upload(local_path="<local>/<name>", remote_path="{{MACE_WORK_DIR}}/<name>")`.

## 5. Select the replay data (multihead only)

One GPU, usually minutes to an hour:

```text
submit_job(spec = launch_plan(..., gpus=1)["job_spec"] + {
    "name": "<name>-select", "directory": "{{MACE_WORK_DIR}}/<name>",
    "executable": "bash select_replay.sh",
    "attributes": {"duration": "02:00:00"}})
```

It must leave `replay/selected_replay.xyz`. Skip this step when
`replay={"selected": ...}` named a file that already exists.

## 6. Train

```text
submit_job(spec = plan["job_spec"] + {
    "name": "<name>", "directory": "{{MACE_WORK_DIR}}/<name>",
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
fs_download(remote_path="{{MACE_WORK_DIR}}/<name>/logs/<name>_run-<seed>.log", ...)
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
submit_job(spec = launch_plan(facility="{{SLUG}}", code="mace", run_type="train",
                              system={"atoms": <atoms per frame>}, gpus=1)["job_spec"] + {
    "name": "<name>-desc", "directory": "<pool folder>",
    "executable": "python mace_descriptors.py --model <base model> --out desc.npy <pool files>",
    "attributes": {"duration": "<walltime>"}})
```

`--dry-run` counts and hashes the pool without a model. `fs_download`
`desc.npy` and `desc.json` and pass `descriptors="desc.npy"`; the tool
refuses descriptors computed on other files.

{{CODE_NOTES}}
