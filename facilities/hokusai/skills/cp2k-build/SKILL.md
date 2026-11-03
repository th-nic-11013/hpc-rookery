---
name: {{SLUG}}-cp2k-build
description: Find, build, and test CP2K (cp2k.psmp, MPI + OpenMP) on {{DISPLAY_NAME}}, which has no CP2K, libxc, libint or ELPA modules. Use whenever the user wants CP2K installed, rebuilt, upgraded, or checked on {{DISPLAY_NAME}}, asks which CP2K builds exist there, or a CP2K job fails with missing shared libraries (libcp2k.so, libmkl_*), "cannot find -levent_core", "no such instruction - vmovw", or cp2k.psmp not found.
---

# CP2K on {{DISPLAY_NAME}}: find a build, or build one

Every tool call uses `facility="{{SLUG}}"`. Preview each command and job
spec and wait for the user's permission (`{{SLUG}}-remote-command`). A
build job spends the project's core time, so state its cost first.

**`references/procedure.md` is the tested build procedure.** It was used
for three CP2K releases plus an Intel variant on this machine, and it lists
four site-specific fixes, each found from a real failed build. Read it in
full before building anything. This file is the summary and the entry point.

## 1. Look for an existing build first

Builds live under `~/software/<folder>/`, one folder per version, each
with a `cp2k_env.sh` beside it:

```bash
ls -d ~/software/*/cp2k_env.sh 2>/dev/null
for e in ~/software/*/cp2k_env.sh; do
  echo "== $e"; ( source "$e" >/dev/null 2>&1; cp2k.psmp --version 2>/dev/null | grep -E "CP2K version|cp2kflags" )
done
```

Known builds (the version log in `references/procedure.md` has dates, jobs
and test results):

| folder | version | toolchain | status |
|---|---|---|---|
| `~/software/cp2k` | 2026.2 | GCC 13.2 + Open MPI 4.1.6 + MKL | full regression suite 5546/5546 passed. **The default for the run skills** |
| `~/software/cp2k_old` | 2025.2 | same | 2-node test matches 2026.2 to 7e-12 Ha |
| `~/software/cp2k_2024.3` | 2024.3 | same | 2-node test passed |
| `~/software/intel-cp2k` | 2026.2 | Intel oneAPI (ifx, Intel MPI, MKL) | smoke test only. Not benchmarked; prefer the GCC build |

If a suitable build exists, stop here: the run skills take its
`cp2k_env.sh`. Confirm that nothing is missing before relying on it (the
answer must be `0`):

```bash
( source ~/software/cp2k/cp2k_env.sh; ldd "$(command -v cp2k.psmp)" | grep -c 'not found' )
```

## 2. Build a new version (only when needed)

These are the site constants. Everything else, `references/procedure.md`
tells you how to read from the source tree of the version being built,
because CP2K changes its build system between releases.

```bash
module purge
module load gcc/13.2.0 openmpi/4.1.6
export MKLROOT=/lustre/opt/x86_64/intel/oneapi/2025.3.0/mkl/2025.3
export LD_LIBRARY_PATH=${MKLROOT}/lib:${LD_LIBRARY_PATH}
# toolchain flags that are always the same here:
#   --mpi-mode=openmpi --with-openmpi=system --math-mode=mkl --with-mkl=system
#   --with-gcc=system --target-cpu=native -j 20
```

Here are the procedure's steps, to be followed in order:

1. **Source:** the release tarball, sha256-checked, unpacked into a new
   `~/software/<folder>`. Use the physical path `/lustre/home/<user>/...`,
   and never unpack over an existing build.
2. **Build system:** case A (`tools/toolchain/build_cp2k.sh`, 2026.2),
   case B (`Makefile` + `arch/`, 2024.3 to 2025.2) or case C (CMake only,
   2026.1, never done here).
3. **Package plan:** run `--dry-run` first, then turn off what isn't wanted
   (`--with-sirius=no` unless SIRIUS is needed).
4. **Pre-download every tarball on the login node**, checking each sha256.
   Compute nodes have no direct internet.
5. **The four site fixes:**
   - a libevent link-name shim;
   - PLUMED `--disable-python`;
   - `-mno-avx512fp16`, because binutils 2.30 doesn't know `vmovw`;
   - a run-time `cp2k_env.sh`.
6. **Build job:** `mpc`, 1 node, 28 cores, `-j 20`, 2 h. Worst case 56
   core-hours; measured 28–38 minutes.
7. **Failures:** the table in the procedure maps each message to its fix.
   Resubmit once at most.
8. **Run-time environment and tests:** write `cp2k_env.sh`, run the `ldd`
   check, a 2-node H2O-32 test, and the regression smoke pass.
9. **Record it:** add the version to the procedure's version log.

Rules the procedure enforces:
- Login nodes are for reading files, `--dry-run` and downloads only.
  Compiling and running CP2K happen in a job.
- Start a new build job from the job script of the same case already on the
  cluster, which has been tested: `~/software/cp2k/build_cp2k_job.sh` (case A) or
  `~/software/cp2k_old/build_cp2k_job.sh` (case B). `fs_view` it, copy it,
  and change paths and flags.

Submitting that job script through `submit_job`: the script carries its own
`#SBATCH` lines for manual use, but under `submit_job` the job spec sets the
resources, and the script runs as a plain bash script.

```json
{
  "name": "cp2k-build-<version>",
  "directory": "/lustre/home/<user>/software/<folder>",
  "executable": "bash build_cp2k_job.sh",
  "resources": {"node_count": 1, "processes_per_node": 1, "cpu_cores_per_process": 28},
  "attributes": {"queue_name": "mpc", "duration": "02:00:00"}
}
```

`bash -n build_cp2k_job.sh` before submitting. If the build needs the proxy
(a tarball that clones subprojects; see the procedure's step 4), the job
script must export it itself:
`export https_proxy=${SLURM_SUBMIT_HOST}:3128 http_proxy=${SLURM_SUBMIT_HOST}:3128`.
Putting the proxy in the job spec's `environment` doesn't work: those
values are quoted, so `$SLURM_SUBMIT_HOST` is never expanded.

## Running any build

```bash
source <folder>/cp2k_env.sh
srun --mpi=pmix --cpus-per-task=<threads> cp2k.psmp -i in.inp -o out.out
```

`srun --mpi=pmix` is verified with this Open MPI. Plain `srun` and
`mpirun` across nodes are not. For the layout (ranks × threads) and job
specs, see `{{SLUG}}-cp2k-single-point`; for measured timings, see
`{{SLUG}}-cp2k-benchmark`.
