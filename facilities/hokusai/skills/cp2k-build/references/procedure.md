# Building CP2K on HOKUSAI BW2 (any version)

> **Adapted for HPC Rookery.** Copied from the rccs-agentic-ai
> `hokusai-hpc-build` skill, where it was written and tested. Read
> `YOUR_USER` as the user's login name, and "the user's go-ahead" as this
> repo's preview-and-permission rule (`hokusai-remote-command`). Where this
> file names other rccs-agentic-ai skills (`hokusai-hpc-system`,
> `hokusai-hpc-cp2k-benchmark`), use `hokusai-reference`,
> `hokusai-submitting-jobs` and `hokusai-cp2k-benchmark` instead.

A procedure for building any CP2K release as `cp2k.psmp` (MPI + OpenMP) on the
MPC nodes. Follow the steps in order. Each step says what to run, what to look
for and what to do with the answer.

**Why a procedure and not a fixed recipe.** CP2K changes its build system,
default packages, file names and package versions between releases. A command
copied from one version silently does the wrong thing in another (see the
version log at the end). So this file fixes only what belongs to the *site*
and tells you how to read everything else from the source tree in front of
you. Do not copy a file name, line number, package version or checksum from
this file or from an older build; read it from the tree.

Status: three versions were built with this on 2026-10-06/07. 2026.2 (case A)
needed four submissions, which is where the four site fixes come from. 2025.2
(case B) was then prepared by following the steps below and built on the first
submission (38 minutes). 2024.3 (case B) did the same (28 minutes), with no new failure.

Contents: site constants · 1 source · 2 build system · 3 package set ·
4 tarballs · 5 site fixes · 6 build job · 7 failures · 8 run and test ·
9 record · version log · untested.

## Site constants (same for every version)

| Part | Choice | Why |
|---|---|---|
| Compiler | `gcc/13.2.0` | system GCC is 8.5; `intel/25.3.0` has no `ifort` (an `ifx` build also works: see "Intel oneAPI variant" in the version log) |
| MPI | `openmpi/4.1.6` module | built with gcc 13.2, UCX, PMIx, Slurm |
| BLAS/LAPACK/ScaLAPACK/FFT | MKL 2025.3 through `MKLROOT` | no separate ScaLAPACK or FFTW needed |
| Everything else | built by CP2K's own toolchain script | no site modules for libint, libxc, ELPA, PLUMED… |

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

- Work under the physical path `/lustre/home/YOUR_USER/...`, not `/home/...`.
- Give each version its own folder (`~/software/cp2k` holds 2026.2,
  `~/software/cp2k_old` holds 2025.2). Never unpack over an existing build.
- Reading files, `--dry-run` and downloads are fine on the front end.
  Compiling and running CP2K happen in a job, and every `sbatch` needs the
  user's go-ahead with the cost stated (`hokusai-hpc-system`, Safety rules).

## Step 1. Get the source

```bash
# releases and their tarballs (name, sha256 digest, URL)
curl -s "https://api.github.com/repos/cp2k/cp2k/releases?per_page=12" | grep -E '"(tag_name|published_at)"'
curl -s "https://api.github.com/repos/cp2k/cp2k/releases/tags/v<VER>" | grep -E '"(name|digest|browser_download_url)"'

mkdir -p /lustre/home/YOUR_USER/software/<folder> && cd /lustre/home/YOUR_USER/software/<folder>
curl -sSL -o cp2k-<VER>.tar.bz2 https://github.com/cp2k/cp2k/releases/download/v<VER>/cp2k-<VER>.tar.bz2
echo "<digest without the sha256: prefix>  cp2k-<VER>.tar.bz2" | sha256sum -c -
tar -xjf cp2k-<VER>.tar.bz2
```

Older releases can show `"digest": null` (2024.3 did), so there is nothing to
compare against. Then run `bzip2 -t cp2k-<VER>.tar.bz2` before unpacking, print
`sha256sum` of the file and write it in the version log, so a later download can
be compared. This proves the archive is intact, not that it is the official one.

Take the release tarball, not a `git clone`: it already contains the DBCSR
sources that older versions keep as a git submodule in `exts/dbcsr`.

Below, `CP2K_ROOT` is the unpacked folder and `TC=${CP2K_ROOT}/tools/toolchain`.

## Step 2. Identify the build system

```bash
cd ${CP2K_ROOT}; ls tools/toolchain/build_cp2k.sh Makefile CMakeLists.txt; ls -d arch
```

| What exists | Case | CP2K itself is built by | Seen in |
|---|---|---|---|
| `tools/toolchain/build_cp2k.sh` | **A** | `./build_cp2k.sh -j N` in the toolchain folder (CMake inside) | 2026.2 |
| no `build_cp2k.sh`, but `Makefile` and `arch/` | **B** | copy generated arch files, then `make ARCH=local VERSION=psmp` | 2025.2, 2024.3 built; 2025.1 looked at only |
| only `CMakeLists.txt` | **C** | CMake by hand | 2026.1 |

Case C has not been done on this site. Run the toolchain (steps 3 to 5 apply
unchanged), then follow the instructions it prints at its end and
`${CP2K_ROOT}/INSTALL.md`, and write down what worked in the version log.

Every later step that differs between the cases says so. Do not run a case A
command in a case B tree or the other way round.

## Step 3. Choose the package set with a dry run

The same flags select different packages in different versions, because the
toolchain's defaults change. Find out what *this* version would build before
anything is downloaded or compiled:

```bash
# after the module and MKLROOT lines above
cd ${TC}
./install_cp2k_toolchain.sh <site flags> --with-plumed=install --dry-run
grep -v DONTUSE install/toolchain.conf        # the plan: __INSTALL__ = built from source, __SYSTEM__ = taken from the site
```

`--dry-run` writes only a few small config files under `install/`.

Then adjust with `--with-<pkg>=install` or `--with-<pkg>=no` and repeat the dry
run until the plan is what is wanted. Decide with the user which features
matter; the 2026.2 binary is the reference set: libint, libxc, libxsmm, COSMA,
ELPA, spglib, libvori, PLUMED (with GSL) and tblite (xTB, D3, D4).

Things to look for in the plan:

- **`with_sirius`**: on by default in 2025.2, off in 2026.2. It drags in
  SpFFT, SpLA, HDF5, libvdwxc and pugixml, and libvdwxc turns off MKL's FFTW
  interface so FFTW gets built from source. Unless SIRIUS is wanted, add
  `--with-sirius=no`.
- **`with_tblite`**: on by default in 2026.2, off in 2025.2, and absent from
  2024.3 (no such option, so no xTB/D3/D4 there). Where it exists, add
  `--with-tblite=install` to get xTB/D3/D4.
- **`with_dbcsr`**: in case B the Makefile builds the DBCSR bundled in
  `exts/dbcsr`, so the toolchain copy is wasted time: add `--with-dbcsr=no`.
  2024.3 has no such option (not in its plan and not in `--help`), so leave the
  flag out there; `./install_cp2k_toolchain.sh --help | grep -- --with-dbcsr`
  tells you. In case A keep the default (the 2026.2 build used the toolchain's DBCSR).
- **`with_openblas`, `with_scalapack`, `with_fftw` show `__INSTALL__` even with
  MKL.** The OpenBLAS tarball is only unpacked to detect the CPU type; the MKL
  step turns ScaLAPACK and FFTW off while the toolchain runs (confirmed for
  2025.2: both steps took 0 seconds). No flag needed, but have their tarballs
  present (step 4) so nothing tries to download.
- A package in the plan that is not in the reference set and that nobody
  asked for: turn it off. Every extra package is extra build time and one more
  thing that can fail.

Keep the final flag list; it goes into the build job unchanged.

## Step 4. Pre-download the dependency tarballs

Compute nodes have no direct internet. Put every tarball into `${TC}/build/`
from the front end, so the job does not depend on the network.

For each package marked `__INSTALL__` in the plan, read its installer:

```bash
cd ${TC}/scripts
for p in <packages from the plan>; do
  f=$(ls stage*/install_$p.sh); echo "== $f"
  grep -n -E '_ver=|_sha|_pkg=|download_pkg' $f
done
```

From that output take, per package, the **file name** and its **sha256**:

- The name is the second argument of `download_pkg_from_cp2k_org`, with the
  `${..._ver}` variables filled in from the same file.
- Some installers set values inside `if`/`case` branches. Take the branch for
  this machine: `linux-x86_64` for CMake, `LIBINT_LMAX` 5 for libint (the
  default) unless another was requested.
- Some installers fetch several files (COSMA also needs COSTA and Tiled-MM).
- The file type can differ between versions: libgrpp is a `.zip` in 2024.3 and
  its checksum variable is `libgrpp_sha`, not `..._sha256`, so a grep for
  `_sha256` alone misses it (`_sha` catches it).
- An installer with no `download_pkg` line needs no tarball (libgrpp in 2025.2
  is part of the CP2K source).

Download from `https://www.cp2k.org/static/downloads/<file>` and **check the
sha256 yourself**: an existing tarball is reused as it is (2025.2 only prints
"is found"). `~/software/cp2k_old/fetch_tarballs.sh` is a worked example:
a list of `sha256 filename` lines and a loop of `curl` + `sha256sum -c`; copy
it and replace the list.

**Check for tarballs that fetch more at build time.** Some source tarballs are
not self-contained:

```bash
for t in ${TC}/build/*.t*; do
  l=$(tar -tf "$t" 2>/dev/null)
  for w in $(echo "$l" | grep -E '^[^/]+/subprojects/[^/]+\.wrap$'); do
    echo "$l" | grep -q "^${w%.wrap}/." || echo "not bundled: $w   ($(basename $t))"
  done
done
```

Each `not bundled` line is a git repository that the package clones while it
configures (the `.wrap` file holds the URL). tblite 0.4.0 in 2025.2 prints six
such lines; tblite 0.6.0 in 2026.2 prints none, because its tarball carries
the subprojects. With any such line the build job needs the proxy and should
test it first, see step 6.

## Step 5. Apply the four site fixes

These come from the site, not from a CP2K version, so every version needs
them. What changes is *where* each goes. For every fix: find the place with
the command given, apply it, and confirm with the check.

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

2. **PLUMED and the missing Python headers.** Symptom: `Python.h: No such file
   or directory` in the PLUMED build (seen with PLUMED 2.10.1). Find the
   configure call and add `--disable-python` if it is not there:

   ```bash
   grep -n -A10 '\./configure' ${TC}/scripts/stage*/install_plumed.sh
   ```

   Check: the same grep now shows `--disable-python`. Skip if PLUMED is not in
   the plan. PLUMED 2.9.3 (2025.2) built with the flag in place; whether it
   would have failed without it was not tried.

3. **`Error: no such instruction: vmovw`.** Cause: GCC 13 with `-march=native`
   emits AVX512-FP16 instructions on these CPUs and the system assembler
   (binutils 2.30) does not know them. Fix: put `-mno-avx512fp16` next to
   `-march=native` in the place that sets the flags for **CP2K itself**:

   - Case A: `${CP2K_ROOT}/cmake/CompilerConfiguration.cmake`.
     `grep -n -E 'march=native' ${CP2K_ROOT}/cmake/CompilerConfiguration.cmake`
     shows the GNU lines. 2026.2 has one variable, `_CP2K_GNU_NATIVE_TUNE`;
     append `;-mno-avx512fp16` to its value. Versions without that variable
     have the flags written into each GNU line; append there (not yet tried).
   - Case B: CMake is not used. The flags come from
     `${TC}/scripts/generate_arch_files.sh`:
     `grep -n -E 'march=\$\{TARGET_CPU\}' ${TC}/scripts/generate_arch_files.sh`.
     Edit the line of the GNU branch (the one with `-fno-omit-frame-pointer
     -fopenmp`, not the Intel or AMD lines): add `-mno-avx512fp16` after
     `-mtune=${TARGET_CPU}`. Check after the toolchain has run:
     `grep -c -- -mno-avx512fp16 ${CP2K_ROOT}/arch/local.psmp` must not be 0.
     Confirmed for 2025.2 and 2024.3: every compile line carried the flag and
     no `vmovw` error appeared. The 2024.3 line to edit is identical to the
     2025.2 one, so the same `sed` pattern applies unchanged.

   The dependencies need nothing as long as
   `grep -n 'CFLAGS=' ${TC}/scripts/stage0/setup_buildtools.sh` shows only
   `-mtune=${TARGET_CPU}` and no `-march` (true for 2025.2 and 2026.2).

4. **Run-time environment.** Symptoms at run time: `libcp2k.so...: cannot open
   shared object file`, or `libmkl_sequential.so.2` not found. MKL came from
   `MKLROOT`, not from a module, so nothing adds it for you. Seen in case A
   (2026.2); the case B binary (2025.2) carries its library paths and did not
   need it. Either way, write a `cp2k_env.sh` next to the build and run the
   `ldd` check, both in step 8.

Make fixes 2 and 3 so they can run twice without harm (`grep -q ... || sed -i
...`) and stop the job if the pattern was not found; both build jobs named in
step 6 do this.

## Step 6. Write and submit the build job

Start from the job script of the same case and change paths, flags and the
build part: `~/software/cp2k/build_cp2k_job.sh` (case A) or
`~/software/cp2k_old/build_cp2k_job.sh` (case B).

Request: `mpc`, 1 node, 1 task, 28 cores (which brings 28 GiB), `--time=2:00:00`,
`-j 20` so memory-hungry compiles stay inside 28 GiB. Worst case 56
core-hours. Measured: 2026.2 dependencies about 35 minutes and CP2K 9;
2025.2 dependencies 29 minutes and CP2K 8 (libint and libxc are the long ones).

Order inside the job:

1. `module purge`, the two modules, `MKLROOT`, `LD_LIBRARY_PATH`; save
   `module list` next to the build.
2. Fixes 1 to 3 with their checks.
3. Only if step 4 printed a `not bundled` line: set the proxy and test it
   before anything expensive, so a dead proxy costs one minute and not an hour.

   ```bash
   export https_proxy=${SLURM_SUBMIT_HOST}:3128
   export http_proxy=${SLURM_SUBMIT_HOST}:3128
   timeout 60 git ls-remote --tags <one URL from a .wrap file> | grep -q . || { echo "no proxy"; exit 1; }
   ```

   Verified on 2026-10-07 (the 2025.2 build job): `git ls-remote` to GitHub through
   `hokusai3:3128` works from an MPC node.
4. `cd ${TC}` and run `./install_cp2k_toolchain.sh` with the flags from step 3
   (without `--dry-run`).
5. Build CP2K:
   - Case A: `./build_cp2k.sh -j 20`. Binary: `${CP2K_ROOT}/install/bin/cp2k.psmp`.
   - Case B:

     ```bash
     cp ${TC}/install/arch/* ${CP2K_ROOT}/arch/
     source ${TC}/install/setup
     cd ${CP2K_ROOT} && make -j 20 ARCH=local VERSION=psmp
     ```

     Binary: `${CP2K_ROOT}/exe/local/cp2k.psmp`. The Makefile calls `python3`;
     the system 3.6.8 is enough (2025.2 documents 3.5+ and built with it).
     A line `cmp: .../obj/git-ref: No such file or directory` at the start of
     `make` is harmless.
6. A smoke test: `cp2k.psmp --version`, then `benchmarks/QS/H2O-32.inp` on one
   rank and 4 threads in a folder under `$TMPDIR`, with a timeout. Launch with
   `mpiexec -n 1 --bind-to none`. It shows that the binary starts and links,
   nothing more.

Follow every toolchain or build command with `|| { echo FAILED; exit 1; }` so
the job stops at the first failure instead of running on.

Before `sbatch`: `bash -n` the script, tell the user partition, cores,
walltime and worst-case core-hours, and wait for the go-ahead. Check the job
about once a minute at most, and leave no poller running on the front end.

## Step 7. When the job fails

Read the end of the job log, then the package's own log in
`${TC}/build/<package>-<version>/` (`configure.log`, `make.log`, `cmake.log`).

| Message | Cause and fix |
|---|---|
| `cannot find -levent_core` / `-levent_pthreads` | fix 1 missing or `LIBRARY_PATH` not exported in the job |
| `Python.h: No such file or directory` | fix 2 |
| `no such instruction: vmovw` | fix 3, or it was put in the place for the other case |
| `failed to download ...` | a tarball is missing from `${TC}/build/` (step 4), or a package fetches subprojects and the proxy lines are missing (step 6.3) |
| `Checksum of ... could not be verified` | wrong or damaged tarball; fetch it again and compare the sha256 with the installer |
| `Unknown flag: --with-...` | that option does not exist in this version; see `./install_cp2k_toolchain.sh --help` |
| `build_cp2k.sh: No such file` | this is case B or C, not A (step 2) |
| `libcp2k.so...` or `libmkl_...so` not found when running | fix 4 |

Fix the cause and resubmit once; the toolchain skips packages that already
installed, so a rerun is much shorter. A failure that is not in the table:
stop, report it to the user with the log lines, and after it is solved add a
row here. Do not resubmit the same failing job a second time.

## Step 8. Run-time environment and tests

Write `<folder>/cp2k_env.sh`. Case A (model: `~/software/cp2k/cp2k_env.sh`):

```bash
type module > /dev/null 2>&1 || . /etc/profile
module purge
source ${CP2K_ROOT}/install/cp2k_env     # loads the modules and the toolchain libraries, sets PATH
export MKLROOT=/lustre/opt/x86_64/intel/oneapi/2025.3.0/mkl/2025.3
export LD_LIBRARY_PATH=${MKLROOT}/lib:${LD_LIBRARY_PATH}
```

Case B (model: `~/software/cp2k_old/cp2k_env.sh`):

```bash
type module > /dev/null 2>&1 || . /etc/profile
module purge
module load gcc/13.2.0 openmpi/4.1.6
export PATH=${CP2K_ROOT}/exe/local:${PATH}
```

Whichever case, confirm on the front end before any job that nothing is
missing; the answer must be 0:

```bash
( source <folder>/cp2k_env.sh; ldd "$(command -v cp2k.psmp)" | grep -c 'not found' )
```

In case A source `install/cp2k_env`, not `tools/toolchain/install/setup`: only
the first knows about `libcp2k.so`, and MKL has to be added by hand. In case B
the binary carries its library paths, MKL included, and its data folder, so
the two modules and `PATH` are enough: the 2025.2 two-node test
ran with exactly that. `install/setup` holds build-time settings; the
toolchain's advice to source it before running is not needed here. If the
`ldd` check prints anything but 0, add the missing folder to
`LD_LIBRARY_PATH` in `cp2k_env.sh`.

Run in a job script:

```bash
source <folder>/cp2k_env.sh
export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK}
srun --mpi=pmix --cpus-per-task=${SLURM_CPUS_PER_TASK} cp2k.psmp -i in.inp -o out.out
```

`srun --mpi=pmix` is verified for this Open MPI (the two-node tests of 2026.2 and
2025.2): 8 ranks across 2 nodes, each bound to its own 2 cores. Plain `srun` without
`--mpi=pmix` and `mpirun` across nodes were not tested. `mpiexec -n 2
--bind-to none` works inside a one-node allocation.

Tests, smallest first, each a separate job that needs its own go-ahead:

1. Two-node run of `benchmarks/QS/H2O-32.inp`, 4 ranks per node × 2 threads
   (models: `test_cp2k_job.sh` in `~/software/cp2k` and `~/software/cp2k_old`).
   16 cores, 15-minute limit, about 40 seconds in practice. Look for
   `PROGRAM ENDED`, 10 converged SCF runs and a steady conserved quantity, and
   compare the 11 total energies with an earlier build: 2025.2 and 2026.2
   agree to 7e-12 Ha, first energy -550.518621259 Ha.
2. Regression smoke pass, one MPC node, 28 cores (model:
   `~/software/cp2k/regsmoke_cp2k_job.sh`):

   ```bash
   /usr/bin/python3.12 ${CP2K_ROOT}/tests/do_regtest.py \
       --smoketest --mpiranks 2 --ompthreads 2 --maxtasks 28 \
       --mpiexec "mpiexec -n {N} --bind-to none" \
       --workbasedir ${TMPDIR:-/tmp}/cp2k-regtest-${SLURM_JOB_ID} \
       <binary folder> psmp
   ```

   `<binary folder>` is `${CP2K_ROOT}/install/bin` in case A and
   `${CP2K_ROOT}/exe/local` in case B. Check the options against
   `do_regtest.py --help` of this version first. Call `/usr/bin/python3.12`
   explicitly: the script needed Python ≥ 3.9 in 2026.2 and the system
   `python3` is 3.6. Keep the work tree on node-local `/tmp`, not Lustre: the
   suite copies the whole `tests/` folder and writes thousands of small files.
3. Full regression suite, optional (model: `~/software/cp2k/regtest_cp2k_job_40.sh`).
   The same command without `--smoketest`. Measured for 2026.2: 5546 tests,
   22 min on one node with 40 cores (`--ntasks=40 --mem=40G --maxtasks 40`,
   10 tests at a time), 14.7 core-hours. Budget about 25 core-hours and a
   1 h limit; do not extrapolate from the smoke pass, which came out about
   twice too high. It runs on one node only: `do_regtest.py` starts each test
   with `mpiexec` and keeps its work tree on node-local `/tmp`, so a 2-node
   job gives node 2 nothing. To use more cores, split the directories over two
   one-node jobs with `--restrictdir` and `--skipdir` (not tried; check how
   they take their values first).

## Step 9. Record the result

Add the version to the log below: date, job IDs, case, flags, what differed
from this procedure and how it was solved. Mark anything written down before a
job has confirmed it as *unconfirmed*; remove the mark once a job confirms the
line, and correct the line if the job contradicted it.
A procedure step that was wrong is fixed in the step itself, not only noted in
the log.

## Version log

### 2026.2 (case A), built 2026-10-06/07 in `~/software/cp2k`

- Flags: site flags + `--with-plumed=install`. Four submissions were needed;
  each failure is one of the four fixes.
- `cp2kflags`: omp libint fftw3 libxc elpa parallel scalapack mpi_f08 cosma
  libxs libxsmm plumed2 spglib mkl libdftd4 s_dftd3 tblite libvori libbqb
- Test: H2O-32 on 2 nodes, 4 ranks per node × 2 threads, 28 s
  wall, 10 MD steps, every SCF converged, conserved quantity steady at
  -550.3836 ± 0.0007 Ha.
- Regression smoke pass: 465 of 465 correct in 6 minutes
  (about 2.8 core-hours).
- Full regression suite (`regtest_cp2k_job_40.sh`): 5546 of 5546
  correct, 0 wrong, 0 failed, on one node with 40 cores (10 tests at a time, 2
  ranks × 2 threads each) in 22 min 1 s, 14.7 core-hours. The planned limit
  was 2:30; the estimate before the run (30 to 56 core-hours, 0.7 to 1.4 h) was
  about twice too high, so scaling the smoke pass by test count overestimates.
  `regtest_cp2k_job.sh` is the 28-core, 3 h version of the same script and was
  never submitted.
- Files: `build_cp2k_job.sh`, `test_cp2k_job.sh`, `regsmoke_cp2k_job.sh`,
  `regtest_cp2k_job.sh`, `regtest_cp2k_job_40.sh`, `cp2k_env.sh`, `shim-lib/`,
  logs `cp2k-*_<jobid>.log`, `regtest_<jobid>/error_summary`.
- Layout benchmark (2026-10-17, one exclusive MPC node, `~/software/cp2k/bench`,
  details in its `results.md`): **16 ranks x 7 threads per node is fastest**.
  H2O-64 0.94 s/step (1.30x vs 112 x 1), H2O-128 2.14 s/step (1.34x), UiO-66
  primitive cell (114 atoms, revPBE-D3, 450 Ry) 5.14 s/step (1.07x; 8 x 14 and
  28 x 4 within 3 %). A 4-node run of the
  456-atom UiO-66 cell did not converge its SCF and gives no timing.

### 2025.2 (case B), prepared 2026-10-07 in `~/software/cp2k_old`

- Flags: site flags + `--with-plumed=install --with-sirius=no
  --with-tblite=install --with-dbcsr=no`.
- Differences from 2026.2 found before building: no `build_cp2k.sh`; fix 3
  goes into `generate_arch_files.sh`; SIRIUS on and tblite off by default;
  tblite 0.4.0 does not bundle its six subprojects and clones them while
  configuring (proxy needed; git through it works); PLUMED is 2.9.3; libint
  2.6.0, ELPA 2024.05.001.
- Build job: completed on the first submission, 37 min 38 s on 28
  cores (17.6 core-hours). None of the four known failures appeared.
- `cp2kflags`: omp libint fftw3 libxc libgrpp elpa parallel scalapack mpi_f08
  cosma xsmm plumed2 spglib mkl libdftd4 mctc-lib tblite libvori libbqb
- Smoke test in the same job: H2O-32, 1 rank × 4 threads, 82 s, 10 MD steps,
  every SCF converged. The first three total energies agree with the 2026.2
  two-node test to better than 1e-9 Ha (-550.518621259, -550.515991618,
  -550.520698100).
- Test: H2O-32 on 2 nodes, 4 ranks per node × 2 threads, with
  only `cp2k_env.sh` (modules and `PATH`) sourced. 29.6 s wall (2026.2: 28.3 s),
  10 MD steps, every SCF converged. All 11 total energies agree with the 2026.2
  test to 7e-12 Ha and the conserved quantity is identical to the printed
  precision (it stays between -550.38458 and -550.38324 Ha).
- Not done yet: the regression smoke pass.
- Files: `fetch_tarballs.sh`, `build_cp2k_job.sh`, `test_cp2k_job.sh`,
  `cp2k_env.sh`, `shim-lib/`, logs `cp2k-old-*_<jobid>.log`,
  `smoke_H2O_<jobid>.out`.

### 2024.3 (case B), built 2026-10-07 in `~/software/cp2k_2024.3`

- Flags: site flags + `--with-plumed=install --with-sirius=no`. No
  `--with-tblite` and no `--with-dbcsr`: 2024.3 has neither option.
- Differences from 2025.2 found before building: SIRIUS on by default (turned
  off); no tblite, so no proxy and no `not bundled` tarballs; PLUMED 2.9.0, libint
  2.6.0, libxc 6.2.2, ELPA 2024.03.001, COSMA 2.6.6, GSL 2.7, spglib 2.3.1,
  CMake 3.30.0, OpenBLAS 0.3.27; libgrpp is a `.zip` with its checksum in
  `libgrpp_sha`. GitHub gave no digest for the source tarball; its sha256 is
  `a6eeee773b6b1fb417def576e4049a89a08a0ed5feffcd7f0b33c7d7b48f19ba`
  (integrity checked with `bzip2 -t` only).
- Build job: completed on the first submission, 27 min 46 s on 28
  cores (about 13 core-hours). Toolchain 19 min (libint 518 s the longest),
  CP2K 7 min. None of the four known failures appeared.
- `cp2kflags`: omp libint fftw3 libxc libgrpp elpa parallel scalapack mpi_f08
  cosma xsmm plumed2 spglib mkl libvori libbqb
- Smoke test in the same job: H2O-32, 1 rank × 4 threads, 10 MD steps, every
  SCF converged, first energy -550.518621259 Ha (same as 2025.2 and 2026.2).
- Test: H2O-32 on 2 nodes, 4 ranks per node × 2 threads, with
  only `cp2k_env.sh` sourced, 0 libraries not found. 56 s job wall, 40.8 s in
  CP2K (2025.2: 29.6 s, 2026.2: 28.3 s; those two are whole-run wall times, so
  the gap is not a like-for-like comparison and is unexplained). 10 MD steps,
  every SCF converged; the 11 energies match the 2026.2 test, first three
  checked to about 1e-10 Ha, the rest not diffed.
- Not done: the regression smoke pass (not required by the user).
- Files: `fetch_tarballs.sh`, `build_cp2k_job.sh`, `test_cp2k_job.sh`,
  `cp2k_env.sh`, `shim-lib/`, logs `cp2k-2024.3-*_<jobid>.log`,
  `smoke_H2O_<jobid>.out`.

### 2026.2, Intel oneAPI variant (case A), built 2026-10-27 in `~/software/intel-cp2k`

The procedure above is for GCC. This variant swaps the site constants:
`module load gcc/13.2.0 intel/25.3.0` (gcc only for a modern libstdc++ under
`icx`/`ifx`; the intel module pulls in `intelmpi/impi_25.3.0`), and toolchain
flags `--mpi-mode=intelmpi --with-intelmpi=system --with-intel --with-ifx=yes
--math-mode=mkl --with-mkl=system --with-plumed=install --target-cpu=native -j 20`.
ifx 2025.3.0, Intel MPI 2021.17, MKL 2025.3.

- **None of the four GCC site fixes was needed** (no libevent, PLUMED-Python or
  assembler failure; run time works with the toolchain's `install/cp2k_env`).
- **One new fix, ELPA threading check (the first build job failed):** ELPA's configure
  runs a small MPI program to test the threading level; under Intel MPI inside
  the job it answered "no", and configure stops when OpenMP is on. Cause not
  investigated (possibly the test cannot start MPI in the job). Fix: in
  `${TC}/scripts/stage5/install_elpa.sh` add
  `--enable-runtime-threading-support-checks --enable-allow-thread-limiting
  --without-threading-support-check-during-build` to the configure line (keep a
  `.orig` copy). ELPA then checks at run time and falls back to 1 OpenMP thread
  inside ELPA if the MPI threading level is too low. Whether that happens at
  16 x 7 is unchecked.
- **Second build job:** built in 37 min on 28 cores (toolchain 20 min with the
  earlier libraries reused, ELPA 9 min; CP2K 9 min; smoke run 7 min), 17.3
  core-h. Binary `cp2k-2026.2/install/bin/cp2k.psmp`; cp2kflags include `ifx`,
  `elpa`, `cosma`, `plumed2`, `tblite`, `mkl`.
- **Smoke run** (H2O-32, `mpirun -n 1`, 4 threads): all 11 MD energies match
  the GCC two-node test to 4e-11 Ha. Not a speed test (406 s on 1 x 4 against
  28 s on 2 nodes x 8 x 2).
- Not done: regression suite, ELPA thread check at 16 x 7, benchmark against
  the GCC build at 16 x 7.
- Files: `build_cp2k_job.sh`, `install_elpa.sh.orig`, `dryrun.log`,
  `modules_at_build.txt`, logs `cp2k-intel-build_<jobid>.log`,
  `smoke_H2O_<jobid>.out`.

### Other tags (looked at on GitHub only, 2026-10-07)

2026.1 is case C. 2025.1 is case B.

## Still untested

- Case C (2026.1), and fix 3 in CMake files without `_CP2K_GNU_NATIVE_TUNE`.
- Multi-node scaling: the single-node layout is measured (see the 2026.2 log),
  but the only multi-node test (UiO-66, 456 atoms, 4 nodes) had an unconverged
  SCF, so it measures nothing.
- 2026.2: `libcp2k.so` links both `libmkl_sequential` and `libmkl_gnu_thread`.
  Threaded layouts (16 x 7, 8 x 14) beat pure MPI in the layout benchmark, so
  it does not visibly break OpenMP scaling; which MKL is active is still unchecked.
- The full regression suite for 2025.2 and 2024.3 (done for 2026.2 only).
- A build without fix 3, for comparison: it cannot be made on this site, so
  the full 2026.2 pass is only indirect evidence that the flag changes no result.
- LMC nodes (different CPU) and GPU nodes; no GPU support is compiled in.
