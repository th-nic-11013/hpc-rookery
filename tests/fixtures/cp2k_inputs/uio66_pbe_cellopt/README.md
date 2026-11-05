# UiO-66 PBE-D3(BJ) cell optimisation (source of G18)

The user's hand-written `CELL_OPT` of the 456-atom UiO-66 conventional cell, run on
HOKUSAI 2026-10-29 (converged in 46 BFGS steps, a = 20.889 Å). Copied unchanged from
`~/software/cp2k/hse06_uio66/02_pbe_opt/{opt,restart}.inp` and `00_prep/cell.inc`.

`cp2k_make_input` and `cp2k_make_subsys` are tested against the parts they reproduce:
the `&CELL_OPT` section (keep flags, force tolerances, pressure), the
`@IF ${RESTART} == 1` / `&EXT_RESTART` block, and `SYMMETRY CUBIC` in `&CELL`.
The DFT block, includes and absolute paths are this run's own and are not compared.
Don't edit.
