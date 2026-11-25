## HOKUSAI notes

- **Builds:** `~/software/mlip/2026.10/lammps-cpu/bin/lmp_mpi` and, with
  D3 (EXTRA-PAIR), `lammps-cpu-d3/bin/lmp_mpi`; both take
  `source ~/software/mlip/2026.10/mlip_env.sh`. The D3 build runs plain
  metatomic inputs too, so round-2/3 used it for everything. Exports
  (`mtt`, `mace`) run in the same environment.
- **Layout:** partition `mpc`, 1 rank x 56 threads, bound to cores
  (`srun --mpi=pmix --ntasks=1 --cpus-per-task=56 --cpu-bind=cores`,
  `OMP_PLACES=cores OMP_PROC_BIND=close PMIX_MCA_psec=native`); a run uses
  half a node, so two runs share one.
- **Measured speed:** UiO-66 primitive (456 atoms), round-2 fine-tune,
  float32: 0.20-0.33 s/step at 0.5 fs (static holds faster, collapsed
  cells slower): 40,000 steps (20 ps) fit in a 7 h request, 100,000
  steps in ~10 h. Eight runs (round 3 step A, jobs 9922244-9922251) cost
  ~33 node-h. Float64 is ~1.7x slower.
- **Export job:** select_head_cpu + `mtt train` + check on one `mpc`
  node took minutes (job 9922229); request 30 min and 16 GB.
- **PLUMED:** `lmp_mpi` links `libplumed.so` directly, so
  `PLUMED_KERNEL` is ignored; for the OPES build put
  `LD_LIBRARY_PATH=${MLIP_STACK}/plumed-opes/lib` in front (jobs
  9748509, 9749277).
- **Run script tail** (so `fs_tail` answers "did it finish"):

```bash
srun ... "${LMP}" -in in.npt.lmp -log log.lammps -screen none 2> srun.err
rc=$?; grep -q "^ERROR" log.lammps && rc=1
echo "--- frames: $(grep -c "ITEM: TIMESTEP" frames.dump 2>/dev/null)"
grep -E "^ERROR|^Loop time|Performance" log.lammps
exit ${rc}
```

- **Known model behaviour (UiO-66, not a LAMMPS fault):** MACE-MP-0+D3
  and the round-1/2 fine-tunes collapse the framework under pressure
  (round 2: between 1.0 and 1.25 GPa at 300 K, not reversed within 30 ps
  of decompression). A collapse in a new run is a result to report and
  sample, not a crash to retry.
