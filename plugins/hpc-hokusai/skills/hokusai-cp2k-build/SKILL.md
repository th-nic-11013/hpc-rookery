---
name: hokusai-cp2k-build
description: Find, build, and test CP2K (cp2k.psmp, MPI + OpenMP) on HOKUSAI BigWaterfall2 (HBW2), and record the build. Use whenever the user wants CP2K installed, rebuilt, upgraded, or checked on HOKUSAI BigWaterfall2 (HBW2), asks which CP2K builds exist there or which one the run skills use, or a CP2K job fails with missing shared libraries (libcp2k.so, libmkl_*), a toolchain or link error while building, or cp2k.psmp not found.
---

# CP2K on HOKUSAI BigWaterfall2 (HBW2): find a build, or build one

Every tool call uses `facility="hokusai"`. Preview each command and job
spec and wait for the user's permission (`hokusai-remote-command`). A
build job spends the project's core time, so state its cost first.

This file is the entry point. The method is in three references:
- `references/procedure.md`: the build procedure, steps 1 to 9, for any
  CP2K release on any Slurm machine. **Read it in full before building.**
- `references/versions.md`: what differs between CP2K releases (build
  system, toolchain defaults, test reference energies).
- `references/facility.md`: the HOKUSAI BigWaterfall2 (HBW2) values: modules, flags,
  the site fixes, failures seen here, build times.

Builds are `software_registry` records, read and written through
`hokusai-records`. The run skills take the build to use from there.

## Contract

- **Reads:** `software_registry` (which builds exist, which is the default,
  which job script to start from).
- **Writes:** `software_registry`, one record per finished build (user
  scope: install paths are personal).
- **Tools:** `run_command_on_cluster` (read files, `--dry-run`, downloads,
  `ldd`), `fs_view`, `submit_job`, `get_job_status`, `fs_tail`, and
  `hokusai-records` locally.
- **Preconditions:** the user's go-ahead for each job, with its cost.
- **Done when:** the `ldd` check prints 0, the tests of procedure step 8
  pass, and the build is appended to `software_registry`; or the failure is
  reported with its log lines.

## 1. Look for an existing build first

Ask the registry:

```bash
records.py query software_registry code=cp2k \
    --fields=version,variant.exe,toolchain,env_script,data_dir,build_system,test_status.level,test_status.result,default
```

(`records.py` is `hokusai-records`' script; run it the way that skill
says.) The record with `default: true` is the build the run skills use when
the user names none. Prefer a GCC build that passed a regression or
two-node test; a build tested only by a smoke run is a fallback.

If no record matches but the user says a build exists, look on the
cluster (`ls -d ~/software/*/cp2k_env.sh`) and, if it checks out, record
it (step 3 below) instead of rebuilding.

If a suitable build exists, stop here. Confirm that nothing is missing
before relying on it (the answer must be `0`):

```bash
( source <env_script>; ldd "$(command -v cp2k.psmp)" | grep -c 'not found' )
```

## 2. Build a new version (only when needed)

Follow `references/procedure.md`. In short:

1. **Source:** the release tarball, checked, unpacked into a new folder.
   Never unpack over an existing build.
2. **Build system:** case A (`tools/toolchain/build_cp2k.sh`), B
   (`Makefile` + `arch/`) or C (CMake only). `references/versions.md`
   lists the case of each release seen so far.
3. **Package plan:** `--dry-run` first, then turn off what isn't wanted.
4. **Pre-download every tarball on the login node**, checking each sha256.
5. **Site fixes:** the ones `references/facility.md` lists.
6. **Build job:** `mpc`, 1 node, 28
   cores, `-j 20`, `02:00:00`.
7. **Failures:** the tables map each message to its fix. Resubmit once at
   most.
8. **Run-time environment and tests:** `cp2k_env.sh`, the `ldd` check, a
   2-node H2O-32 test, the regression smoke pass.
9. **Record it** (step 3 below).

Rules the procedure enforces:
- Login nodes are for reading files, `--dry-run` and downloads only.
  Compiling and running CP2K happen in a job.
- Start a new build job from a tested job script of the same case: the
  `build_cp2k_job.sh` in the `install_path` of a registry build whose
  `build_system` matches (`toolchain-script` = case A, `make` = case B).
  `fs_view` it, copy it, and change paths and flags.

Submitting that job script through `submit_job`: the script carries its own
`#SBATCH` lines for manual use, but under `submit_job` the job spec sets the
resources, and the script runs as a plain bash script.

```json
{
  "name": "cp2k-build-<version>",
  "directory": "<absolute path of the new build folder>",
  "executable": "bash build_cp2k_job.sh",
  "resources": {"node_count": 1, "processes_per_node": 1, "cpu_cores_per_process": 28},
  "attributes": {"queue_name": "mpc", "duration": "02:00:00"}
}
```

`bash -n build_cp2k_job.sh` before submitting. Anything the job needs from
the environment (a proxy, for instance) is exported inside the script:
values in the job spec's `environment` are quoted, so `$VAR` there is
never expanded.

## 3. Record the build

Append one `software_registry` record through `hokusai-records`:
- `build_id` `<code>-<version>-<exe>-<target>-<toolchain-short>`, e.g.
  `cp2k-2026.2-psmp-cpu-gcc`;
- `variant.libs` from the `cp2kflags` line of `cp2k.psmp --version`;
- `install_path`, `env_script`, `data_dir` (case A:
  `<source folder>/data`), `build_system`;
- `build_job_id`, and `test_status` with the highest test that ran, its
  result and job IDs;
- `default: false`. Moving the default to the new build means changing
  the old record too, which an append can't do (`build_id` is the record's
  id). If the user wants the switch, say so and leave that edit to them;
  `hokusai-records` says where the file is.

Then add what this build taught about the procedure: a CP2K-version fact
goes in `references/versions.md`, a site fact in `references/facility.md`,
and a wrong step is fixed in `references/procedure.md` itself. Those are
repository files: tell the user the change, so it reaches the repo.

## Running any build

```bash
source <env_script>
srun --mpi=pmix --cpus-per-task=<threads> cp2k.psmp -i in.inp -o out.out
```

For the layout (ranks × threads) and job specs, see
`hokusai-cp2k-single-point`; for measured timings, see
`hokusai-cp2k-benchmark`.

### On HOKUSAI BigWaterfall2 (HBW2)

- **No CP2K, libxc, libint or ELPA modules:** every build is made with
  CP2K's toolchain script, on `gcc/13.2.0` + `openmpi/4.1.6` + MKL 2025.3
  from `MKLROOT` (`references/facility.md` has the exact lines).
- **Four site fixes**, needed by every GCC build: a libevent link-name
  shim (`cannot find -levent_core`), PLUMED `--disable-python`
  (`Python.h`), `-mno-avx512fp16` (`no such instruction: vmovw`, binutils
  2.30), and `MKLROOT` in the run-time `cp2k_env.sh` (`libmkl_*` /
  `libcp2k.so` not found).
- **Proxy:** compute nodes have no internet; a build that clones
  subprojects exports `${SLURM_SUBMIT_HOST}:3128` inside the job script.
- **Cost:** a build is 28–38 minutes on 28 cores (worst case 56
  core-hours); a full regression suite about 15 core-hours.
