# MACE predictions on the six cp2k_labels frames (fixtures for mlip_compare)

Written on HOKUSAI by the mace pack's `codes/mace/eval/scripts/predict.py` (job 9771064, 2026-11-12, `mpc`,
1 × 28 cores, ~25 s per model, mace-torch 0.3.14) on `cp2k_make_labels`' extxyz of `tests/fixtures/cp2k_labels/`
(config_type = the frame name), gzipped unchanged (`gzip -9 -n`). Folder:
`~/agent/work/mace-finetune/eval-t1-repro/`.

| file | model (`~/software/mlip/2026.10/models/`) | `--dispersion` |
|---|---|---|
| `matpes_r2scan.predictions.json.gz` | `mace-matpes-r2scan-0/MACE-matpes-r2scan-omat-ft.model` | none |
| `mp0_medium.predictions.json.gz` | `mace-mp-0-medium/2023-12-03-mace-128-L1_epoch-199.model` | none |

The labels include D3(BJ), the models don't: `mlip_compare(..., d3_mismatch="energy_only")` reproduces T1
(job 9751103, `~/agent/work/mlmd-uio66/matpes-test/eval.json`) to < 1e-3 meV. Don't edit.
