---
name: hokusai-cp2k-single-point
description: Run a CP2K single-point energy and forces calculation on HOKUSAI BigWaterfall2 (HBW2) - write the input, parse-check it, submit with the measured MPI x OpenMP layout, and check SCF convergence. Use whenever the user wants an energy, forces, a wavefunction, a gap, or reference data (e.g. MLIP training labels) for a fixed geometry with CP2K on HOKUSAI BigWaterfall2 (HBW2), or a first test that a CP2K setup converges for a new system.
---

# CP2K single point on HOKUSAI BigWaterfall2 (HBW2)

Every tool call uses `facility="hokusai"`. Show the user the input's key
settings and the job spec, state the cost, and wait for permission before
submitting.

## Contract

- **Reads:** `benchmarks` (the layout), through `hokusai-records`.
- **Writes:** no records. It writes `single_point.out` and, if asked, the
  wavefunction.
- **Tools:** `fs_upload`, `run_command_on_cluster` (parse check),
  `submit_job`, `get_job_status`, `fs_tail` / `fs_view`.
- **Preconditions:** the two prerequisites below, and the user's approval
  of the input and the cost.
- **Done when:** the SCF converged and the energy (and forces) are
  reported, or the failure is reported with no number quoted.

**Prerequisites:**
- **A CP2K build:** its `env_script` (written `<env_script>` below) from
  `software_registry`, through `hokusai-records`. Unless the user names a
  build, take the default:
  `records_query(facility="hokusai", record_type="software_registry", filters={"code": "cp2k", "default": true}, fields=["env_script", "data_dir"])`.
  Builds are made by `hokusai-cp2k-build`.
- **`subsys.inc`** for the structure, from `hokusai-structure-making` and
  its `cp2k/cp2k.md`.

## 1. Write the input

Copy `templates/single_point.inp` next to `subsys.inc` and set its `@SET`
lines. Its DFT settings are the ones run and benchmarked on HOKUSAI BigWaterfall2 (HBW2):
- revPBE-D3 (zero damping), `DZVP-MOLOPT-SR-GTH` with `GTH-PBE-q<N>`;
- 450/60 Ry, Gamma point;
- OT DIIS with `PRECONDITIONER FULL_ALL` and `ENERGY_GAP 0.05`.

That SCF converged a 456-atom MOF cell from the atomic guess in 55
steps. The older default, `FULL_SINGLE_INVERSE`, stalled on the same cell.

Ask the user about anything that changes the physics:

| setting | template | change when |
|---|---|---|
| `CHARGE`, `MULTIPLICITY`, `UKS` | 0, 1, `.FALSE.` | the user says so. Odd electron counts and open shells need `UKS .TRUE.` |
| functional | revPBE-D3 | plain PBE-D3: `PARAMETRIZATION ORIG`, `D3_REFERENCE PBE`. Hybrids or SCAN need other basis/potential files and ADMM. Ask first |
| `CUTOFF` / `REL_CUTOFF` | 450 / 60 Ry | converge for production: run 350–700 Ry and take the value where the energy changes < 1 meV/atom (and forces < 1e-4 Ha/bohr) |
| SCF | OT DIIS + FULL_ALL | metals and small-gap systems: replace with the diagonalisation + smearing block given in the template's comments |
| isolated molecule | periodic | `subsys.inc` made with `--periodic NONE`: uncomment `&POISSON` |

The template has **no `IGNORE_CONVERGENCE_FAILURE`**. An unconverged SCF
must stop the run, not produce an energy that looks valid.

Check the syntax on the login node before any job. A serial parse takes a
few seconds:

```bash
( source <env_script>; cd <run dir>; OMP_NUM_THREADS=1 timeout 60 cp2k.psmp --check -i single_point.inp )
```

This proves the syntax, including the `@INCLUDE`, but not that the SCF
converges.

## 2. Stage and submit

```text
~/agent/work/cp2k-runs/<system>/sp/   single_point.inp  subsys.inc
```

Get the absolute home path once (`echo $HOME`); `directory` is passed to
`sbatch --chdir`, which doesn't expand `~`.

```json
{
  "name": "cp2k-sp-<system>",
  "directory": "/home/<user>/agent/work/cp2k-runs/<system>/sp",
  "pre_launch": "source <env_script>",
  "launcher": "srun --mpi=pmix --cpus-per-task=<T> --cpu-bind=cores --distribution=block:block",
  "executable": "cp2k.psmp -i single_point.inp -o single_point.out",
  "environment": {"OMP_NUM_THREADS": "<T>", "OMP_PLACES": "cores", "OMP_PROC_BIND": "close"},
  "resources": {"node_count": <N>, "processes_per_node": <R>, "cpu_cores_per_process": <T>},
  "attributes": {"queue_name": "mpc", "duration": "01:00:00"}
}
```

### Layout: R ranks × T threads per node, from benchmark records

Don't guess the layout; read it. Through `hokusai-records`:

1. Query `benchmarks` with `code=cp2k`, `run_type=single_point`,
   `system.label=<this system>` and `valid=true`, and take the fastest.
   Layouts within 3% of it are ties; any of them will do.
2. If that finds nothing, query by `code=cp2k`, `run_type=single_point` and
   `partition=mpc`. Use the fastest layout among systems with a similar size
   and the same method (`method.functional`, `method.cutoff_ry`, the kind of
   SCF).
3. If still nothing matches, say so. Then either use the fastest
   `single_point` layout on the partition as a provisional choice, or
   suggest `hokusai-cp2k-benchmark` first if many single points will run.

Don't reuse an `aimd_step` layout for single points: the two workloads rank
layouts differently. Tell the user which record `id` the layout came from.

A node has 112 cores, so R × T = 112
for a full node. The rules every layout follows:

- **The launcher carries the binding.** `--cpus-per-task=<T>` must be
  repeated there, because `srun` doesn't inherit it (Slurm 22.05 and later). With
  `--cpu-bind=cores --distribution=block:block` plus `OMP_PLACES=cores` and
  `OMP_PROC_BIND=close`, rank r owns cores r×T to r×T+T−1. Change all of
  `cpus-per-task`, `OMP_NUM_THREADS` and `cpu_cores_per_process` together.
- **`srun --mpi=pmix` is required:** it selects the MPI the build
  was linked against.
- **Memory:** memory per rank × ranks per NUMA domain must stay well under
  one domain's memory; CP2K prints `Estimated peak process memory`.
- **Several nodes:** keep the per-node layout and set `node_count`. Query
  `benchmarks` at other node counts for the parallel efficiency
  (`extra.parallel_efficiency`). Use more nodes only when wall time matters.

### On HOKUSAI BigWaterfall2 (HBW2)

- **Node:** 112 cores in 8 NUMA domains of 14, ~16 GB of
  HBM per domain, no DDR. The layout and binding details are in
  `hokusai-cp2k-benchmark`'s `references/facility.md`.
- **Memory:** leave `resources.memory` unset. All 112 cores bring 112 GiB,
  and memory above 1 GiB/core is billed as extra cores.
- **MPI:** the builds use the site Open MPI, which needs `--mpi=pmix`.
- **Limits:** 64 nodes and 24 h on `mpc` (`mpc_l`: 8 nodes, 72 h). A job
  over a limit is accepted but never starts.

Tell the user the worst-case cost: nodes × 112 × duration in hours, in
core-hours. A matched record's `metric.value` (`wall_s`) gives the expected
run time.

## 3. Check the result

`fs_tail` / `fs_view` on `single_point.out`:

| look for | meaning |
|---|---|
| `PROGRAM ENDED AT` | finished |
| `*** SCF run converged in N steps ***` | converged |
| `SCF run NOT converged`, or a run that stops on an SCF error | **not converged: no usable energy.** Report it; don't quote a number |
| `ENERGY\| Total FORCE_EVAL ( QS ) energy [hartree]` | the total energy |
| `FORCES\|` lines (atom, x, y, z, \|f\|, in hartree/bohr) | the forces. CP2K 2026.2 prints them to the main output in this form; older versions use an `ATOMIC FORCES in [a.u.]` block |
| `The number of warnings for this run is : N` | N > 0: find the `*** WARNING` lines and report them |
| `ABORT` | fatal. The reason is in the box above it |

Common failures:

| symptom | fix |
|---|---|
| `no ... for <element>` from `make_subsys.py`, or a basis/potential not found | see `structure-making/cp2k/cp2k.md` |
| `error while loading shared libraries`, exit 127 | the job didn't source `cp2k_env.sh` |
| SCF stalls (OT gradient flat at 1e-5) | check charge and multiplicity. Then try OT CG, or diagonalisation + Broyden (both converged the 456-atom cell) |
| `module: command not found` | the job was submitted with `--export=NONE`. Don't |

Save the wavefunction (`<PROJECT>-RESTART.wfn`) when a follow-up
calculation on the same geometry will need it, and start that from it with
`SCF_GUESS RESTART`.
