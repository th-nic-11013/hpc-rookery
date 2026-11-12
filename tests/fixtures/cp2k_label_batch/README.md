# The E1 r²SCAN label input (fixture for cp2k_make_label_batch)

`sp-g1-r2scan.inp`: the input of HOKUSAI job 9749330 (`~/agent/work/mlmd-uio66/e1-label-sens/g1/r2scan/`,
2026-11-08), whose output is `tests/fixtures/cp2k_labels/g1.out.gz`. Written by `cp2k_make_input` then;
downloaded unchanged (fs_download, sha256 15a0463d…) on 2026-11-12.

The G51 check compares the batch writer's `label.inp` against it: the same DFT settings (UZH 2026.2 SCAN-GTH files,
CUTOFF 1000 / REL_CUTOFF 60 Ry, EPS_DEFAULT 1e-12, OT DIIS + FULL_ALL, EPS_SCF 1e-6), apart from D3(BJ) (dropped:
the campaign's labels are D3-free), PROJECT, WALLTIME and print-only blocks (MO_CUBES, &RESTART). Don't edit.
