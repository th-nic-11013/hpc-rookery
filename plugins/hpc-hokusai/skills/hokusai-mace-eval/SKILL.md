---
name: hokusai-mace-eval
description: Score MACE models against labelled frames on HOKUSAI BigWaterfall2 (HBW2) - run each model on the frames of a labelled extxyz in a short job, download the predictions and compare them with the labels (relative and absolute energies, force RMSE and cosine, stress, worst frames, per config_type). Use whenever the user wants to test, evaluate, validate, score or benchmark the accuracy of a foundation or fine-tuned MLIP, a held-out test set, an error map before fine-tuning, or an active-learning round - even if they only say "how good is the model on these frames".
---

# Scoring MACE models on HOKUSAI BigWaterfall2 (HBW2)

Every cluster tool call uses `facility="hokusai"`. Show the user the job
spec and its cost, and wait for permission before the submit.

## Contract

- **Reads:** a labelled extxyz on this machine, the pack's model table
  (facility notes at the end), `software_registry` (the `mace` build).
- **Writes:** a folder `~/agent/work/mace-finetune/eval-<name>/` on the cluster
  with the labels, `predict.py` and one `<model>.predictions.json` per
  model; locally only what you download and the report, if asked.
- **Tools:** `mlip_check_dataset`, `mlip_compare` (the hyogo server; local),
  `fs_mkdir`, `fs_upload`, `render_job_script`, `submit_job`,
  `get_job_status`, `fs_tail`, `fs_download`; `launch_plan` for a GPU spec.
- **Preconditions:** labels (`cp2k_make_labels`), the user's approval of
  the job.
- **Done when:** `mlip_compare` returns the errors per model and they are
  reported with the warnings; or the failure is reported with no number
  quoted.

## 1. Check the labels

```text
mlip_check_dataset(paths=["labels.extxyz"])
```

Note `d3`. A model trained on D3-free labels (the MatPES and MP foundation
models, a model fine-tuned on D3-free labels) is scored against D3-free
labels. `mlip_compare` refuses the other pairings; with D3 labels and a
D3-free model its `d3_mismatch="energy_only"` compares energies without D3
and leaves D3 in the force and stress errors (upper bounds).

## 2. Run the models

`scripts/predict.py` (beside this skill) runs every `--model` on every
frame and writes `<name>.predictions.json` (energy, forces, stress, the
model's E0s, keyed by `source_sha256` and the atoms in file order). It
adds and subtracts nothing. `--dispersion` states what the models'
energies contain: `none` or `included`; there is no default.

```text
fs_mkdir("~/agent/work/mace-finetune/eval-<name>")
fs_upload(local_path="labels.extxyz", remote_path="~/agent/work/mace-finetune/eval-<name>/labels.extxyz")
fs_upload(local_path="<this skill>/scripts/predict.py", remote_path="~/agent/work/mace-finetune/eval-<name>/predict.py")
spec = {"name": "eval-<name>", "directory": "~/agent/work/mace-finetune/eval-<name>",
        "pre_launch": "source <the mace build's env_script>",
        "executable": "python predict.py labels.extxyz --out-dir . --dispersion none "
                      "--model <name>=<model path> [--model ...]",
        ... CPU or GPU resources: the facility notes below ...}
render_job_script(facility="hokusai", spec=spec)     # show it, ask
submit_job(facility="hokusai", spec=spec)
```

On a GPU add `--device cuda`; `launch_plan(code="mace", run_type="single_point",
system={"atoms": <n>}, gpus=1)` gives the resources and launch line.
A model with several heads (a multihead fine-tune: `Default`, `pt_head`)
takes `--head`; the default is the model's first head.

## 3. Compare

```text
fs_download(remote_path="~/agent/work/mace-finetune/eval-<name>/<model>.predictions.json", ...)   # each model
mlip_compare(labels=["labels.extxyz"], predictions=[<the files>],
             reference_frame=<index or a config_type naming one frame>,
             e0s=<the labels' e0s.json, for absolute energies>)
```

Report per model and per config_type: the relative energy error
(meV/atom, against the reference frame), the absolute one after E0s when
`e0s` was given, force RMSE and cosine, stress RMSE, and the worst frames.
Read every warning. `out_path` keeps the full report with every frame.

## HOKUSAI notes

- **Where to run:** a few to a few hundred frames of ~500 atoms run on
  CPU in minutes: partition `mpc`, 1 node, 1 process with 28 cores
  (`OMP_NUM_THREADS=28`), 30 min. T1 (job 9751103: two float64 models,
  six 456-atom frames) took about 2 minutes. The `gpu` queue often waits
  many hours (see the mace-finetune notes); use it only for thousands of
  frames.
- **Environment:** `source ~/software/mlip/2026.10/mlip_env.sh`
  (mace-torch 0.3.14, ase, torch; the `mace` build's `env_script`).
- **Models:** the table in the mace-finetune notes. Both foundation models
  are float64 and D3-free: `--dispersion none`. The MP-0 head is `Default`,
  MatPES's `default`; leaving `--head` out takes each model's own.
- **No D3 on the cluster's Python side:** torch-dftd is not installed, so
  D3 forces can't be subtracted from labels that include them. Label
  D3-free frames instead.
