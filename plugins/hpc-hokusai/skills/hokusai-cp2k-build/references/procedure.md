# Building CP2K (any version)

A procedure for building any CP2K release as `cp2k.psmp` (MPI + OpenMP)
with CP2K's own toolchain script on a Slurm cluster. Follow the steps in
order. Each step says what to run, what to look for and what to do with the
answer.

Read alongside it:
- `facility.md` (next to this file): this facility's values. Wherever a
  step says **site**, the value is there: modules, math library, toolchain
  flags, the site fixes, the build job's size, the proxy.
- `versions.md`: what is known about each CP2K release.

**Why a procedure and not a fixed recipe.** CP2K changes its build system,
default packages, file names and package versions between releases. A
command copied from one version silently does the wrong thing in another
(see `versions.md`). So this file fixes only the method, and tells you how
to read everything else from the source tree in front of you. Do not copy a
file name, line number, package version or checksum from this file or from
an older build; read it from the tree.

History: written and tested on HOKUSAI BW2 for 2026.2 (case A, four
submissions, which is where that site's four fixes come from), then 2025.2
and 2024.3 (case B, each built on the first submission by following these
steps), and an Intel oneAPI variant of 2026.2.

Contents: 0 site setup · 1 source · 2 build system · 3 package set ·
4 tarballs · 5 site fixes · 6 build job · 7 failures · 8 run and test ·
9 record.

## Step 0. Site setup (same for every version)

The site's choices of compiler, MPI and math library, and the toolchain
flags that follow from them, are in `facility.md`. Typical shape:

| Part | Choice |
|---|---|
| Compiler | a module with a modern GCC (the system GCC is often too old) |
| MPI | the site's MPI module, built with that compiler, with PMIx and Slurm support |
| BLAS/LAPACK/ScaLAPACK/FFT | MKL (`--math-mode=mkl --with-mkl=system`), so no separate ScaLAPACK or FFTW |
| Everything else | built by CP2K's own toolchain script (libint, libxc, ELPA, PLUMED…) |

- Work under the physical path of the home or software folder, not a
  symlink (the toolchain records absolute paths).
- Give each version its own folder. Never unpack over an existing build.
- Reading files, `--dry-run` and downloads are fine on the login node.
  Compiling and running CP2K happen in a job, and every job needs the
  user's go-ahead with the cost stated.

## Step 1. Get the source

```bash
# releases and their tarballs (name, sha256 digest, URL)
curl -s "https://api.github.com/repos/cp2k/cp2k/releases?per_page=12" | grep -E '"(tag_name|published_at)"'
curl -s "https://api.github.com/repos/cp2k/cp2k/releases/tags/v<VER>" | grep -E '"(name|digest|browser_download_url)"'

mkdir -p <build folder> && cd <build folder>
curl -sSL -o cp2k-<VER>.tar.bz2 https://github.com/cp2k/cp2k/releases/download/v<VER>/cp2k-<VER>.tar.bz2
echo "<digest without the sha256: prefix>  cp2k-<VER>.tar.bz2" | sha256sum -c -
tar -xjf cp2k-<VER>.tar.bz2
```

Older releases can show `"digest": null`, so there is nothing to compare
against. Then run `bzip2 -t cp2k-<VER>.tar.bz2` before unpacking, compare
`sha256sum` of the file with `versions.md`, and add it there if it is
missing, so a later download can be compared. This proves the archive is
intact, not that it is the official one.

Take the release tarball, not a `git clone`: it already contains the DBCSR
sources that older versions keep as a git submodule in `exts/dbcsr`.

Below, `CP2K_ROOT` is the unpacked folder and `TC=${CP2K_ROOT}/tools/toolchain`.

## Step 2. Identify the build system

```bash
cd ${CP2K_ROOT}; ls tools/toolchain/build_cp2k.sh Makefile CMakeLists.txt; ls -d arch
```

| What exists | Case | CP2K itself is built by |
|---|---|---|
| `tools/toolchain/build_cp2k.sh` | **A** | `./build_cp2k.sh -j N` in the toolchain folder (CMake inside) |
| no `build_cp2k.sh`, but `Makefile` and `arch/` | **B** | copy generated arch files, then `make ARCH=local VERSION=psmp` |
| only `CMakeLists.txt` | **C** | CMake by hand |

`versions.md` gives the case of each release seen so far. Case C has not
been built yet. Run the toolchain (steps 3 to 5 apply unchanged), then
follow the instructions it prints at its end and `${CP2K_ROOT}/INSTALL.md`,
and write down what worked in `versions.md`.

Every later step that differs between the cases says so. Do not run a case
A command in a case B tree or the other way round.

## Step 3. Choose the package set with a dry run

The same flags select different packages in different versions, because the
toolchain's defaults change. Find out what *this* version would build before
anything is downloaded or compiled:

```bash
# after the site module lines (facility.md)
cd ${TC}
./install_cp2k_toolchain.sh <site flags> --with-plumed=install --dry-run
grep -v DONTUSE install/toolchain.conf        # the plan: __INSTALL__ = built from source, __SYSTEM__ = taken from the site
```

`--dry-run` writes only a few small config files under `install/`.

Then adjust with `--with-<pkg>=install` or `--with-<pkg>=no` and repeat the
dry run until the plan is what is wanted. Decide with the user which
features matter. The reference set (the 2026.2 builds) is libint, libxc,
libxsmm, COSMA, ELPA, spglib, libvori, PLUMED (with GSL) and tblite (xTB,
D3, D4).

Things to look for in the plan (per-version defaults are in `versions.md`):

- **`with_sirius`**: it drags in SpFFT, SpLA, HDF5, libvdwxc and pugixml,
  and libvdwxc turns off MKL's FFTW interface so FFTW gets built from
  source. Unless SIRIUS is wanted, add `--with-sirius=no`.
- **`with_tblite`**: where the option exists, add `--with-tblite=install` to
  get xTB/D3/D4.
- **`with_dbcsr`**: in case B the Makefile builds the DBCSR bundled in
  `exts/dbcsr`, so the toolchain copy is wasted time: add `--with-dbcsr=no`
  where the option exists (`./install_cp2k_toolchain.sh --help | grep --
  --with-dbcsr` tells you). In case A keep the default.
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

Compute nodes often have no direct internet (`facility.md` says). Put every
tarball into `${TC}/build/` from the login node, so the job does not depend
on the network.

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
  this machine: `linux-x86_64` for CMake on x86, `LIBINT_LMAX` 5 for libint
  (the default) unless another was requested.
- Some installers fetch several files (COSMA also needs COSTA and Tiled-MM).
- The file type and the checksum variable's name can differ between
  versions (`versions.md`), so grep for `_sha`, not `_sha256`.
- An installer with no `download_pkg` line needs no tarball.

Download from `https://www.cp2k.org/static/downloads/<file>` and **check the
sha256 yourself**: an existing tarball is reused as it is. A small script
does it: a list of `sha256 filename` lines and a loop of `curl` +
`sha256sum -c` over it. (The tested copy, `fetch_tarballs.sh` of the HOKUSAI
2025.2 build, was deleted with that build.)

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
configures (the `.wrap` file holds the URL). With any such line the build job
needs the site proxy and should test it first, see step 6.

## Step 5. Apply the site fixes

Some failures come from the site (its assembler, its missing devel packages,
how its MPI was built), not from a CP2K version, so every version on that
site needs the fix. `facility.md` lists them, each with its symptom, the
command that finds where it goes, and a check. What changes between versions
is *where* a fix goes, usually by case:

- **Flags for CP2K itself** (e.g. an extra `-mno-...` next to
  `-march=native`):
  - Case A: `${CP2K_ROOT}/cmake/CompilerConfiguration.cmake`;
    `grep -n -E 'march=native'` there shows the GNU lines. Some versions set
    one variable (2026.2: `_CP2K_GNU_NATIVE_TUNE`; append `;<flag>` to its
    value); others write the flags into each GNU line (not yet tried).
  - Case B: CMake is not used; the flags come from
    `${TC}/scripts/generate_arch_files.sh`:
    `grep -n -E 'march=\$\{TARGET_CPU\}'` there. Edit the line of the GNU
    branch (the one with `-fno-omit-frame-pointer -fopenmp`, not the Intel
    or AMD lines): add the flag after `-mtune=${TARGET_CPU}`. Check after the
    toolchain has run: `grep -c -- <flag> ${CP2K_ROOT}/arch/local.psmp` must
    not be 0.
  - The dependencies need nothing as long as
    `grep -n 'CFLAGS=' ${TC}/scripts/stage0/setup_buildtools.sh` shows only
    `-mtune=${TARGET_CPU}` and no `-march`.
- **A package's configure options** (e.g. PLUMED `--disable-python`, ELPA
  threading checks): edit its `${TC}/scripts/stage*/install_<pkg>.sh`, found
  with `grep -n -A10 '\./configure'`, and keep a `.orig` copy.
- **Link or run-time library paths:** exported in the build job, or written
  into `cp2k_env.sh` (step 8).

Make every edit so it can run twice without harm (`grep -q ... || sed -i
...`) and stop the job if the pattern was not found; the tested build jobs
do this.

## Step 6. Write and submit the build job

Start from a tested job script of the same case and change paths, flags and
the build part: the `build_cp2k_job.sh` in the `install_path` of a registry
build whose `build_system` matches and that is not `removed`. If there is
none, write the job from the order below.

Request: the site's build partition, 1 node, 1 task, the cores and walltime
in `facility.md`, and a `-j` low enough that memory-hungry compiles fit in
the memory those cores bring. libint and libxc are the long packages.

Order inside the job:

1. `module purge`, the site modules and exports (`facility.md`); save
   `module list` next to the build.
2. The site fixes with their checks.
3. Only if step 4 printed a `not bundled` line: set the site proxy and test
   it before anything expensive, so a dead proxy costs one minute and not an
   hour:

   ```bash
   export https_proxy=<site proxy> http_proxy=<site proxy>
   timeout 60 git ls-remote --tags <one URL from a .wrap file> | grep -q . || { echo "no proxy"; exit 1; }
   ```

4. `cd ${TC}` and run `./install_cp2k_toolchain.sh` with the flags from step 3
   (without `--dry-run`).
5. Build CP2K:
   - Case A: `./build_cp2k.sh -j <N>`. Binary: `${CP2K_ROOT}/install/bin/cp2k.psmp`.
   - Case B:

     ```bash
     cp ${TC}/install/arch/* ${CP2K_ROOT}/arch/
     source ${TC}/install/setup
     cd ${CP2K_ROOT} && make -j <N> ARCH=local VERSION=psmp
     ```

     Binary: `${CP2K_ROOT}/exe/local/cp2k.psmp`. The Makefile calls
     `python3`; an old system Python 3 is enough (2025.2 documents 3.5+). A
     line `cmp: .../obj/git-ref: No such file or directory` at the start of
     `make` is harmless.
6. A smoke test: `cp2k.psmp --version`, then `benchmarks/QS/H2O-32.inp` on one
   rank and 4 threads in a folder under `$TMPDIR`, with a timeout. Launch with
   `mpiexec -n 1 --bind-to none` (Open MPI) or `mpirun -n 1` (Intel MPI). It
   shows that the binary starts and links, nothing more.

Follow every toolchain or build command with `|| { echo FAILED; exit 1; }` so
the job stops at the first failure instead of running on.

Before submitting: `bash -n` the script, tell the user partition, cores,
walltime and worst-case core-hours, and wait for the go-ahead. Check the job
about once a minute at most, and leave no poller running on the login node.

## Step 7. When the job fails

Read the end of the job log, then the package's own log in
`${TC}/build/<package>-<version>/` (`configure.log`, `make.log`, `cmake.log`).
`facility.md` has the failures seen on this site; these are general:

| Message | Cause and fix |
|---|---|
| `failed to download ...` | a tarball is missing from `${TC}/build/` (step 4), or a package fetches subprojects and the proxy lines are missing (step 6.3) |
| `Checksum of ... could not be verified` | wrong or damaged tarball; fetch it again and compare the sha256 with the installer |
| `Unknown flag: --with-...` | that option does not exist in this version; see `./install_cp2k_toolchain.sh --help` |
| `build_cp2k.sh: No such file` | this is case B or C, not A (step 2) |
| `libcp2k.so...` or `libmkl_...so` not found when running | the run-time environment (step 8) |

Fix the cause and resubmit once; the toolchain skips packages that already
installed, so a rerun is much shorter. A failure that is in neither table:
stop, report it to the user with the log lines, and after it is solved add a
row to `facility.md` (a site cause) or here (a general one). Do not resubmit
the same failing job a second time.

## Step 8. Run-time environment and tests

Write `<build folder>/cp2k_env.sh`. Start from the `cp2k_env.sh` of a
registry build with the same `build_system`. The shapes:

Case A:

```bash
type module > /dev/null 2>&1 || . /etc/profile
module purge
source ${CP2K_ROOT}/install/cp2k_env     # loads the modules and the toolchain libraries, sets PATH
# plus any site exports facility.md lists (e.g. a math library taken from MKLROOT, not a module)
```

Case B:

```bash
type module > /dev/null 2>&1 || . /etc/profile
module purge
module load <site compiler and MPI modules>
export PATH=${CP2K_ROOT}/exe/local:${PATH}
```

Whichever case, confirm on the login node before any job that nothing is
missing; the answer must be 0:

```bash
( source <build folder>/cp2k_env.sh; ldd "$(command -v cp2k.psmp)" | grep -c 'not found' )
```

In case A source `install/cp2k_env`, not `tools/toolchain/install/setup`:
only the first knows about `libcp2k.so`. In case B the binary carries its
library paths and its data folder, so the modules and `PATH` are enough.
`install/setup` holds build-time settings; the toolchain's advice to source
it before running is not needed. If the `ldd` check prints anything but 0,
add the missing folder to `LD_LIBRARY_PATH` in `cp2k_env.sh`.

Run in a job script:

```bash
source <build folder>/cp2k_env.sh
export OMP_NUM_THREADS=${SLURM_CPUS_PER_TASK}
srun <site srun MPI flag> --cpus-per-task=${SLURM_CPUS_PER_TASK} cp2k.psmp -i in.inp -o out.out
```

Tests, smallest first, each a separate job that needs its own go-ahead:

1. Two-node run of `benchmarks/QS/H2O-32.inp`, 4 ranks per node × 2 threads
   (model: the `test_cp2k_job.sh` of a registry build). 16 cores, 15-minute
   limit, under a minute in practice. Look for `PROGRAM ENDED`, 10 converged
   SCF runs and a steady conserved quantity, and compare the 11 total
   energies with `versions.md`.
2. Regression smoke pass, one node, 28 cores (model: the
   `regsmoke_cp2k_job.sh` of the case A registry build):

   ```bash
   <site python ≥ 3.9> ${CP2K_ROOT}/tests/do_regtest.py \
       --smoketest --mpiranks 2 --ompthreads 2 --maxtasks 28 \
       --mpiexec "mpiexec -n {N} --bind-to none" \
       --workbasedir ${TMPDIR:-/tmp}/cp2k-regtest-${SLURM_JOB_ID} \
       <binary folder> psmp
   ```

   `<binary folder>` is `${CP2K_ROOT}/install/bin` in case A and
   `${CP2K_ROOT}/exe/local` in case B. Check the options against
   `do_regtest.py --help` of this version first. Keep the work tree on
   node-local `/tmp`, not a parallel file system: the suite copies the whole
   `tests/` folder and writes thousands of small files.
3. Full regression suite, optional (model: the `regtest_cp2k_job_40.sh` of
   the case A registry build). The same command without `--smoketest`. For
   2026.2: 5546 tests, 22 min on one node with 40 cores (`--ntasks=40
   --mem=40G --maxtasks 40`, 10 tests at a time), 14.7 core-hours. Budget
   about 25 core-hours and a 1 h limit; do not extrapolate from the smoke
   pass, which came out about twice too high. It runs on one node only:
   `do_regtest.py` starts each test with `mpiexec` and keeps its work tree
   on node-local `/tmp`, so a 2-node job gives node 2 nothing. To use more
   cores, split the directories over two one-node jobs with `--restrictdir`
   and `--skipdir` (not tried; check how they take their values first).

## Step 9. Record the result

Append the build to `software_registry` (the skill's step 3): version,
`cp2kflags`, install and env paths, `data_dir`, `build_system`, the build
job ID and the highest test passed. Then put what was learned where it
belongs:
- a fact about the CP2K release (case, defaults, a renamed option, test
  energies) in `versions.md`;
- a fact about the site (a new fix, a failure, build time) in
  `facility.md`;
- a step of this procedure that was wrong: fix it in the step itself.

Mark anything written down before a job has confirmed it as *unconfirmed*;
remove the mark once a job confirms the line, and correct the line if the
job contradicted it.
