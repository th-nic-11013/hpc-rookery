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
A multihead fine-tune lists `pt_head` (the replay head) first; leaving
`--head` out scores its other head (`Default`), never `pt_head`. A model
with several non-replay heads needs `--head`. `--head` applies to every
`--model` in the call, so score models with different head names in
separate calls.

**GPU-trained models on CPU.** A model trained on a GPU (most fine-tunes)
holds e3nn TorchScript pieces pickled with CUDA tensors, and e3nn's
`__setstate__` calls `torch.jit.load` with no `map_location`, so on a CPU
node loading fails with "Found no NVIDIA driver" even though
`MACECalculator(device="cpu")` was asked for. `predict.py` handles this
(`_load_on_cpu`, used by `_build` for every calculator it creates, with or
without `--head`), so CPU scoring of GPU-trained fine-tunes works. Any new
script that loads such a model on CPU needs the same patch; copy it from
`predict.py`. A quick load test on a login node must take the same path as
the job (leave `--head` out if the job does): a first fix that covered only
the `--head` path still failed in the job.

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

`mlip_compare` (and hyogo's other label tools) read local files only. When
the frames or predictions are large, the low-bandwidth option is to run
hyogo's own compare functions inside the job on the cluster and download
only a small summary; the facility notes say how, where it has been done.

## HOKUSAI notes

- **Where to run:** CPU, partition `mpc`, 1 node, 1 process with 28 cores
  (`OMP_NUM_THREADS=28`). The `gpu` queue often waits many hours (see the
  mace-finetune notes); use it only for thousands of frames.
- **Measured CPU times** (28 threads, float64, 456-atom frames, job
  9900725): a float32-trained round-2 fine-tune run in float64 takes
  ~0.5 s/frame (24 frames 13 s, 7 frames 4–5 s); the MACE-MATPES-r2SCAN-0
  foundation ~8 s/frame (35 frames 278 s). Four fine-tunes on 66 frames
  plus the foundation on 35 took 4:49 min including the compare. Request
  1 h and 48 GB; the foundation dominates, so scale by its frame count.
- **Job spec:** nest the resources (flat keys such as `nodes`,
  `cpus_per_task` or `memory` are refused, with where each goes; they used to
  be dropped without a word). Without `memory`, Slurm on `mpc` gives 1 GB and float64
  MACE-MATPES on a few hundred atoms is OOM-killed (job 9895283):

  ```text
  "resources": {"node_count": 1, "process_count": 1,
                "cpu_cores_per_process": 28, "memory": 51539607552},   # 48 GiB, bytes int
  "attributes": {"queue_name": "mpc", "duration": "01:00:00"},
  "environment": {"OMP_NUM_THREADS": "28"}
  ```

  Check that `render_job_script` shows `--mem` and `--cpus-per-task`
  before submitting.
- **Environment:** `source ~/software/mlip/2026.10/mlip_env.sh`
  (mace-torch 0.3.14, ase, torch; the `mace` build's `env_script`).
- **Models:** the table in the mace-finetune notes. Both foundation models
  are float64 and D3-free: `--dispersion none`. The MP-0 head is `Default`,
  MatPES's `default`; leaving `--head` out takes each model's own.
  GPU-trained fine-tunes from `gpu` score fine on `mpc` with `predict.py`.
- **Login-node load tests:** `run_command_on_cluster` feeds no stdin, so
  `python - <<EOF` hangs until the ~120 s SSH limit and leaves a stray
  process. Use `python -c "..."`, and kill leftovers (`ps -u $USER`).
- **Compare on the cluster (low bandwidth):** hyogo is checked out at
  `~/agent/work/tools/hyogo-src`. Inside the job, after `predict.py`, run
  hyogo's compare functions directly (with `hyogo.mcp_server` stubbed in
  `sys.modules`, since the MCP package isn't installed there) and write a
  small `summary.json`; download only that. Reference scripts in the
  hpc-rookery repo: `notes/round2-eval/compare_on_cluster.py` (compare),
  `notes/round2-select/collect_on_cluster.py` (committee/selection).
- **No D3 on the cluster's Python side:** torch-dftd is not installed, so
  D3 forces can't be subtracted from labels that include them. Label
  D3-free frames instead.
