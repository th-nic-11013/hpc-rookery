---
name: {{SLUG}}-mace-eval
description: Score MACE models against labelled frames on {{DISPLAY_NAME}} - run each model on the frames of a labelled extxyz in a short job, download the predictions and compare them with the labels (relative and absolute energies, force RMSE and cosine, stress, worst frames, per config_type). Use whenever the user wants to test, evaluate, validate, score or benchmark the accuracy of a foundation or fine-tuned MLIP, a held-out test set, an error map before fine-tuning, or an active-learning round - even if they only say "how good is the model on these frames".
---

# Scoring MACE models on {{DISPLAY_NAME}}

Every cluster tool call uses `facility="{{SLUG}}"`. Show the user the job
spec and its cost, and wait for permission before the submit.

## Contract

- **Reads:** a labelled extxyz on this machine, the pack's model table
  (facility notes at the end), `software_registry` (the `mace` build).
- **Writes:** a folder `{{MACE_WORK_DIR}}/eval-<name>/` on the cluster
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
fs_mkdir("{{MACE_WORK_DIR}}/eval-<name>")
fs_upload(local_path="labels.extxyz", remote_path="{{MACE_WORK_DIR}}/eval-<name>/labels.extxyz")
fs_upload(local_path="<this skill>/scripts/predict.py", remote_path="{{MACE_WORK_DIR}}/eval-<name>/predict.py")
spec = {"name": "eval-<name>", "directory": "{{MACE_WORK_DIR}}/eval-<name>",
        "pre_launch": "source <the mace build's env_script>",
        "executable": "python predict.py labels.extxyz --out-dir . --dispersion none "
                      "--model <name>=<model path> [--model ...]",
        ... CPU or GPU resources: the facility notes below ...}
render_job_script(facility="{{SLUG}}", spec=spec)     # show it, ask
submit_job(facility="{{SLUG}}", spec=spec)
```

On a GPU add `--device cuda`; `launch_plan(code="mace", run_type="single_point",
system={"atoms": <n>}, gpus=1)` gives the resources and launch line.
A model with several heads (a multihead fine-tune: `Default`, `pt_head`)
takes `--head`; the default is the model's first head.

## 3. Compare

```text
fs_download(remote_path="{{MACE_WORK_DIR}}/eval-<name>/<model>.predictions.json", ...)   # each model
mlip_compare(labels=["labels.extxyz"], predictions=[<the files>],
             reference_frame=<index or a config_type naming one frame>,
             e0s=<the labels' e0s.json, for absolute energies>)
```

Report per model and per config_type: the relative energy error
(meV/atom, against the reference frame), the absolute one after E0s when
`e0s` was given, force RMSE and cosine, stress RMSE, and the worst frames.
Read every warning. `out_path` keeps the full report with every frame.

{{CODE_NOTES}}
