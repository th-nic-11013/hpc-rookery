# MACE fine-tuning fixtures (G49)

The user's own fine-tuning campaigns on another cluster, copied 2026-11-11
from a backup. Home-directory paths were replaced with `/home/user` and the
cluster's name removed; nothing else in `reference/` was edited.

`reference/` (the sources `hyogo/mace/` was built from):
- `02_finetune.slurm`, `03_restart.slurm`, `04_train_from_scratch.slurm`:
  the job scripts of the MeOH/LiTFSI v2 campaign (MACE-MH-1, mace-torch
  0.3.16, finished 2026-10-26: valid 0.3 meV/atom, 10.0 meV/A). Their
  `mace_run_train` argument sets are `mace_make_finetune`'s presets.
- `01_select_replay.slurm`, `select_replay.py`: replay selection.
- `split_train_valid.py`: the seeded 90/10 split.
- `mh1_omat_pbe_lora.yml`: the LoRA run's config (project3).
- `parse_mace.py`: the per-epoch log reader `mace_parse_log` grew from; the
  conformance check runs it on the logs below and compares.

Logs, trimmed: the first 60 lines, then only the lines a parser reads
(banner, version, heads, Initial/Epoch rows, stop, checkpoint, model, error
table, ERROR lines). A run of identical consecutive messages is cut to 3.
- `meoh_v2_multihead.log`: two heads, early stop at epoch 58, best 38. The
  original had 10,000 "Failed to extract energy" and 10,000 "Failed to
  extract forces" ERROR lines (the replay frames); 3 of each are kept.
- `p3_multihead_restarts.log`: 6 sessions (restarts), 150 epochs, best 118.
- `p3_lora.log`: one head, 2 sessions, 150 epochs.
- `hokusai_naive_stress_0.3.14.log`: untrimmed, mace-torch 0.3.14 on one H100
  (HOKUSAI job 9765431, 2026-11-11): a 2-epoch run written by
  `mace_make_finetune` (naive, six UiO-66 frames, stress in the loss, E0s
  `average`). The only log here with stress errors and from 0.3.14.
