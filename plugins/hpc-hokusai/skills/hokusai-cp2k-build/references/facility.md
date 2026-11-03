# Building CP2K on HOKUSAI BW2: site values

The HOKUSAI BigWaterfall2 (HBW2) side of `procedure.md`. Everything here was found on
real build jobs on the MPC nodes, 2026-10-06 to 2026-10-27. The builds
themselves are `software_registry` records.

## Site constants (step 0)

| Part | Choice | Why |
|---|---|---|
| Compiler | `gcc/13.2.0` | system GCC is 8.5; `intel/25.3.0` has no `ifort` (an `ifx` build also works: see the Intel variant below) |
| MPI | `openmpi/4.1.6` module | built with gcc 13.2, UCX, PMIx, Slurm |
| BLAS/LAPACK/ScaLAPACK/FFT | MKL 2025.3 through `MKLROOT` | not a module, so nothing adds it to the run-time paths for you |

```bash
module purge
module load gcc/13.2.0 openmpi/4.1.6
export MKLROOT=/lustre/opt/x86_64/intel/oneapi/2025.3.0/mkl/2025.3
export LD_LIBRARY_PATH=${MKLROOT}/lib:${LD_LIBRARY_PATH}
```

Toolchain flags that are always the same here:

```
--mpi-mode=openmpi --with-openmpi=system --math-mode=mkl --with-mkl=system
--with-gcc=system --target-cpu=native -j 20
```

- Work under the physical path `/lustre/home/<user>/...`, not `/home/...`.
- Builds live under `~/software/<folder>/`, one folder per version.
- Compute nodes have no direct internet (step 4).
- Python: the system `python3` is 3.6.8 (enough for the case B Makefile);
  use `/usr/bin/python3.12` for `do_regtest.py`.

## The four site fixes (step 5)

Needed by every GCC build here; each was found from a failed 2026.2
submission.

1. **Missing libevent link names.** Symptom: `cannot find -levent_core` or
   `-levent_pthreads` while ELPA (or any libtool build) links. Cause: the site
   Open MPI names both as dependencies, but `libevent-devel` is not installed,
   so only the versioned `.so.6` files exist. Fix, identical for all versions:

   ```bash
   mkdir -p <folder>/shim-lib
   ln -sf /usr/lib64/libevent_core-2.1.so.6     <folder>/shim-lib/libevent_core.so
   ln -sf /usr/lib64/libevent_pthreads-2.1.so.6 <folder>/shim-lib/libevent_pthreads.so
   export LIBRARY_PATH=<folder>/shim-lib:${LIBRARY_PATH}     # in the build job
   ```

2. **PLUMED and the missing Python headers.** Symptom: `Python.h: No such
   file or directory` in the PLUMED build (seen with PLUMED 2.10.1). Add
   `--disable-python` to the configure call in
   `${TC}/scripts/stage*/install_plumed.sh` if it is not there. Check: the
   same grep now shows `--disable-python`. Skip if PLUMED is not in the
   plan. PLUMED 2.9.3 (2025.2) built with the flag in place; whether it would
   have failed without it was not tried.

3. **`Error: no such instruction: vmovw`.** Cause: GCC 13 with
   `-march=native` emits AVX512-FP16 instructions on these CPUs, and the
   system assembler (binutils 2.30) does not know them. Fix: put
   `-mno-avx512fp16` next to `-march=native` in the place that sets the
   flags for CP2K itself (procedure step 5, by case). Confirmed for 2026.2
   (case A, `_CP2K_GNU_NATIVE_TUNE`) and for 2025.2 and 2024.3 (case B: every
   compile line carried the flag; the line to edit in
   `generate_arch_files.sh` is identical in both). The dependencies need
   nothing (no `-march` in `setup_buildtools.sh` for 2025.2 and 2026.2).

4. **Run-time environment.** Symptoms at run time: `libcp2k.so...: cannot
   open shared object file`, or `libmkl_sequential.so.2` not found. MKL came
   from `MKLROOT`, so case A's `cp2k_env.sh` adds it after
   `install/cp2k_env`:

   ```bash
   export MKLROOT=/lustre/opt/x86_64/intel/oneapi/2025.3.0/mkl/2025.3
   export LD_LIBRARY_PATH=${MKLROOT}/lib:${LD_LIBRARY_PATH}
   ```

   The case B binary (2025.2) carries its library paths and did not need it.

Failures seen here (procedure step 7):

| Message | Cause and fix |
|---|---|
| `cannot find -levent_core` / `-levent_pthreads` | fix 1 missing, or `LIBRARY_PATH` not exported in the job |
| `Python.h: No such file or directory` | fix 2 |
| `no such instruction: vmovw` | fix 3, or it was put in the place for the other case |
| `libcp2k.so...` or `libmkl_...so` not found when running | fix 4 |
| ELPA configure stops on the MPI threading level (Intel MPI) | the Intel variant's ELPA fix below |

## Proxy (step 6.3)

Only when step 4 printed a `not bundled` line (2025.2 with tblite 0.4.0).
Inside the job script:

```bash
export https_proxy=${SLURM_SUBMIT_HOST}:3128
export http_proxy=${SLURM_SUBMIT_HOST}:3128
```

Verified on 2026-10-07 (the 2025.2 build job): `git ls-remote` to GitHub
through the login node's port 3128 works from an MPC node. Not in the job
spec's `environment`: those values are quoted, so `$SLURM_SUBMIT_HOST` is
never expanded.

## Build job and cost (step 6)

`mpc`, 1 node, 1 task, 28 cores (which
bring 28 GiB), `02:00:00`, `-j 20` so
memory-hungry compiles stay inside 28 GiB. Worst case 56 core-hours.

| build | submissions | wall | core-hours | split |
|---|---|---|---|---|
| 2026.2 GCC (case A) | 4 (one per site fix) | ~44 min (sum of the split) | | dependencies ~35 min, CP2K 9 |
| 2025.2 GCC (case B) | 1 | 37 min 38 s | 17.6 | dependencies 29 min, CP2K 8 |
| 2024.3 GCC (case B) | 1 | 27 min 46 s | ~13 | toolchain 19 min (libint 518 s), CP2K 7 |
| 2026.2 Intel (case A) | 2 (ELPA fix) | 37 min | 17.3 | toolchain 20 min with earlier libraries reused (ELPA 9), CP2K 9, smoke run 7 |

Tests here:
- 2-node H2O-32, 4 ranks per node × 2 threads, 16 cores: 28–30 s of wall
  for 2026.2 and 2025.2. 2024.3 took 40.8 s in CP2K (56 s job wall); those
  are not like-for-like and the gap is unexplained.
- Regression smoke pass (2026.2): 465 of 465 correct in 6 minutes, about 2.8
  core-hours.
- Full suite (2026.2): 5546 of 5546 correct in 22 min 1 s on one node with
  40 cores, 14.7 core-hours. The estimate before the run (30 to 56
  core-hours) was about twice too high.
- `srun --mpi=pmix` is verified with this Open MPI (8 ranks across 2
  nodes, each bound to its own 2 cores). Plain `srun` and `mpirun` across
  nodes are not tested. `mpiexec -n 2 --bind-to none` works inside a
  one-node allocation.

## Intel oneAPI variant

The site constants change to:

```bash
module load gcc/13.2.0 intel/25.3.0     # gcc only for a modern libstdc++ under icx/ifx; intel pulls in intelmpi/impi_25.3.0
```

and the toolchain flags to `--mpi-mode=intelmpi --with-intelmpi=system
--with-intel --with-ifx=yes --math-mode=mkl --with-mkl=system
--with-plumed=install --target-cpu=native -j 20` (ifx
2025.3.0, Intel MPI 2021.17, MKL 2025.3).

- **None of the four GCC site fixes is needed** (no libevent, PLUMED-Python
  or assembler failure; run time works with the toolchain's
  `install/cp2k_env`).
- **ELPA threading check (one new fix).** ELPA's configure runs a small MPI
  program to test the threading level; under Intel MPI inside the job it
  answered "no", and configure stops when OpenMP is on. Cause not
  investigated (possibly the test cannot start MPI in the job). Fix: in
  `${TC}/scripts/stage5/install_elpa.sh` add
  `--enable-runtime-threading-support-checks --enable-allow-thread-limiting
  --without-threading-support-check-during-build` to the configure line
  (keep a `.orig` copy). ELPA then checks at run time and falls back to 1
  OpenMP thread inside ELPA if the MPI threading level is too low. Whether
  that happens at the production layout is unchecked.
- Smoke run (H2O-32, `mpirun -n 1`, 4 threads): not a speed test (406 s on
  1 × 4).
- Not done: regression suite, the ELPA thread check at the production
  layout, a benchmark against the GCC build. Prefer the GCC build.

## Still untested here

- A build without fix 3, for comparison: it cannot be made on this site, so
  the full 2026.2 pass is only indirect evidence that the flag changes no
  result.
- LMC nodes (different CPU) and GPU nodes; no GPU support is compiled in.
