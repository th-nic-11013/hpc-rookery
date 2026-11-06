# CP2K outputs from HOKUSAI (fixtures for cp2k_parse_output)

Real outputs of the UiO-66 functional study (456-atom conventional cell,
CP2K 2026.2 psmp on HOKUSAI MPC, 16 ranks x 7 threads per node), gzipped
(`gzip -9 -n`) unchanged. Tests decompress them to a temporary folder.

| file | job | run |
|---|---|---|
| `r2scan-sp.out.gz` | 9737100 | r2SCAN-D3(BJ) energy + forces + stress, 1 node, PBE cell |
| `pbe0-sp.out.gz` | 9737101 | PBE0-D3(BJ) energy + forces + stress, 2 nodes, PBE cell |
| `hse06-sp.out.gz` | 9735210 | HSE06-D3(BJ) energy + forces + stress, 2 nodes, PBE cell |
| `hse06-cellopt.out.gz` | 9737072 | HSE06-D3(BJ) CELL_OPT, 21 BFGS steps, 2 nodes |

All four were written by `cp2k_make_input` (2026-11-05). The expected
values in `tests/conformance.py` were read from these files by hand before
the parser existed. Don't edit.
