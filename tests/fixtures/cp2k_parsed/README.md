# cp2k_parse_output results

hyogo's `cp2k_parse_output` on `tests/fixtures/cp2k_outputs/`, saved as JSON
(made with hyogo at hpc-rookery e187b2c, the last commit before hyogo moved
to its own repository). `cp2k_record_from_run` takes this result as data, so
rookery's check reads these files instead of importing hyogo.

- `r2scan-sp.json`, `hse06-cellopt.json`: the whole `.out`.
- `hse06-cellopt-cut-step12.json`: the cell_opt `.out` cut just before
  `OPTIMIZATION STEP:     12` (a run stopped by the walltime).

Re-make them if hyogo's result format changes.
