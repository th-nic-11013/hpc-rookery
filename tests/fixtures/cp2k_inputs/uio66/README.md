# UiO-66 functional inputs (golden files)

The five single points of [notes/uio66-functionals.md](../../../../notes/uio66-functionals.md),
written by hand with `make_inputs.sh` on 2026-11-04. `cp2k --check` passed on all
five (CP2K 2026.2). `sp_<functional>.inp` is that run's `sp.inp` with its
`xc.inc` inlined. `subsys_*_admm.inc` are the `&SUBSYS` includes with the ADMM
`AUX_FIT` lines that the generator added with sed.

`cp2k_make_input` and `cp2k_make_subsys` are tested against them; don't edit.

**Corrected 2026-11-05** (notes/hokusai-hse06-uio66-review.md, C4/G12). The
hand-written hybrid inputs (`sp_pbe0`, `sp_hse06`, `sp_r2scan0`) broke CP2K's
own HFX rules, so they were fixed here rather than kept as written:
- `EXCH_CORRECTION_FUNC PBEX` → `DEFAULT`. PBEX is a full-range correction;
  DEFAULT matches the HF operator (`hfx_admm_utils.F`: short-range wPBE for
  SHORTRANGE, truncated PBE hole for TRUNCATED);
- `MIN_PAIR_LIST_RADIUS -1` in `&QS`. Without it CP2K warns that the KS
  matrix "is not 100% occupied" and HFX may be wrong (`hfx_energy_potential.F`);
- `EPS_SCHWARZ_FORCES` equal to `EPS_SCHWARZ`. Its default is 100×, and the
  manual says it should be similar to EPS_SCF;
- HSE06: `CUTOFF_RADIUS ${HFX_RADIUS}` on the SHORTRANGE operator. At
  EPS_SCHWARZ 1e-6 it would reach 14.3 Å, past half this cell (5.99 Å);
- `&HF_INFO` printed (memory diagnostics; this doesn't change results).

None of these corrected inputs has been run.
