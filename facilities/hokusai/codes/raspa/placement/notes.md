## HOKUSAI notes

- **Build:** RASPA3 3.0.30, the prebuilt el8 skylake-avx512 RPM unpacked
  under `~/software/mlip/2026.10/raspa3` with an MKL shim for
  libblas/liblapack (`software_registry` `raspa3-3.0.30-other-cpu-prebuilt`,
  jobs 9820144/9820145). `source ~/software/mlip/2026.10/raspa3/raspa3_env.sh`
  before `raspa3`.
- **Layout:** partition `mpc`, one core per run folder; several folders in
  one job, side by side (a placement run in a 114-atom cell with ≤ 30 waters
  takes minutes; 15 runs ≈ 1 core-h, an estimate, not yet measured):

```bash
source ~/software/mlip/2026.10/raspa3/raspa3_env.sh
export OMP_NUM_THREADS=1
for d in N*-s*/; do ( cd "$d" && raspa3 > raspa3.stdout 2> raspa3.stderr; echo "$d exit $?" ) & done
wait
grep -L "Simulation finished" N*-s*/output/output_*.txt   # unfinished runs, if any
```

  Request one core per folder (`ntasks=1`, `cpus_per_task` = folders),
  1 GB per core and 1 h.
