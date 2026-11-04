# UiO-66 functional inputs (golden files)

The five single points of [notes/uio66-functionals.md](../../../../notes/uio66-functionals.md),
written by hand with `make_inputs.sh` on 2026-11-04. `cp2k --check` passed on all
five (CP2K 2026.2). `sp_<functional>.inp` is that run's `sp.inp` with its
`xc.inc` inlined. `subsys_*_admm.inc` are the `&SUBSYS` includes with the ADMM
`AUX_FIT` lines that the generator added with sed.

`cp2k_make_input` and `cp2k_make_subsys` are tested against them; don't edit.
