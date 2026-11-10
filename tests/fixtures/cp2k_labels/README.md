# r²SCAN-D3(BJ) single points on UiO-66 frames (fixtures for cp2k_make_labels)

Real CP2K 2026.2 outputs from HOKUSAI MPC (1 node, 16 ranks × 7 threads), with the subsys.inc each run included,
gzipped unchanged (`gzip -9 -n`). 456-atom UiO-66 cells from MACE-MP-0 MD (compressed frames), labelled for the
fine-tuning label-sensitivity test (E1). `PRINT_LEVEL LOW`, energy + forces + analytical stress in the `.out`.

| files | job | HOKUSAI folder (`~/agent/work/mlmd-uio66/`) | V (Å³) |
|---|---|---|---|
| `f0.*` | 9749293 | `o3-r2scan-frames/f0/` (`sp.out`) | 9040 |
| `f4.*` | 9749294 | `o3-r2scan-frames/f4/` | 7794 |
| `f6.*` | 9749295 | `o3-r2scan-frames/f6/` | 6503 |
| `g1.*` | 9749330 | `e1-label-sens/g1/r2scan/` (`sp-g1-r2scan.out`) | 8118 |
| `g2.*` | 9749332 | `e1-label-sens/g2/r2scan/` | 6839 |
| `g3.*` | 9749333 | `e1-label-sens/g3/r2scan/` | 4805 |

All were written by `cp2k_make_input` (2026-11-08). The expected numbers in `tests/conformance.py` come from the
scratch script `prep_frames.py` used for the T1 base-model test (2026-11-10), written before this tool. Don't edit.
