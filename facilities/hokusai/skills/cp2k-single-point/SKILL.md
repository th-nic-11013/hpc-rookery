---
name: {{SLUG}}-cp2k-single-point
description: Run a CP2K single-point energy and forces calculation on {{DISPLAY_NAME}} - write the input, parse-check it, submit with the measured MPI x OpenMP layout, and check SCF convergence. Use whenever the user wants an energy, forces, a wavefunction, a gap, or reference data (e.g. MLIP training labels) for a fixed geometry with CP2K on {{DISPLAY_NAME}}, or a first test that a CP2K setup converges for a new system.
---

# CP2K single point on {{DISPLAY_NAME}}

Every tool call uses `facility="{{SLUG}}"`. Show the user the input's key
settings and the job spec, state the cost, and wait for permission before
submitting.

**Prerequisites:**
- **A CP2K build** and its `cp2k_env.sh`. The default is
  `~/software/cp2k/cp2k_env.sh` (CP2K 2026.2, GCC + Open MPI + MKL, full
  regression suite passed). Others are listed in `{{SLUG}}-cp2k-build`.
- **`subsys.inc`** for the structure, from `{{SLUG}}-structure-making` and
  its `cp2k/cp2k.md`.

## 1. Write the input

Copy `templates/single_point.inp` next to `subsys.inc` and set its `@SET`
lines. Its DFT settings are the ones run and benchmarked on {{DISPLAY_NAME}}:
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
( source ~/software/cp2k/cp2k_env.sh; cd <run dir>; OMP_NUM_THREADS=1 timeout 60 cp2k.psmp --check -i single_point.inp )
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
  "pre_launch": "source $HOME/software/cp2k/cp2k_env.sh",
  "launcher": "srun --mpi=pmix --cpus-per-task=7 --cpu-bind=cores --distribution=block:block",
  "executable": "cp2k.psmp -i single_point.inp -o single_point.out",
  "environment": {"OMP_NUM_THREADS": "7", "OMP_PLACES": "cores", "OMP_PROC_BIND": "close"},
  "resources": {"node_count": 1, "processes_per_node": 16, "cpu_cores_per_process": 7},
  "attributes": {"queue_name": "mpc", "duration": "01:00:00"}
}
```

### Layout: 16 ranks × 7 threads per node, from measurements

An `mpc` node has 112 cores in **8 NUMA domains of 14 cores** (~16 GB of
HBM each, no DDR). Every layout below keeps each rank inside one domain.
The single-node benchmark results (`{{SLUG}}-cp2k-benchmark`,
`references/results.md`):

| run type | measured on | best | close behind | avoid |
|---|---|---|---|---|
| single point (ENERGY_FORCE) | UiO-66 456 atoms, H2O-128 | **16×7** and 56×2 tie, ~1.27× pure MPI | 28×4, 8×14 | 4×28 (1.6× slower) |
| AIMD step | H2O-64/128, UiO-66 114/456 atoms | **16×7** on 1, 2 and 4 nodes | 8×14, 28×4 | 4×28, 56×2 |

- **The launcher carries the binding.** `--cpus-per-task=<threads>` must be
  repeated there, because Slurm 23.02's `srun` doesn't inherit it. With
  `--cpu-bind=cores --distribution=block:block` plus `OMP_PLACES=cores` and
  `OMP_PROC_BIND=close`, rank r owns cores r×7 to r×7+6. Change all of
  `cpus-per-task`, `OMP_NUM_THREADS` and `cpu_cores_per_process` together.
- **`--mpi=pmix` is required:** the build uses the site Open MPI.
- **Memory:** leave `resources.memory` unset. All 112 cores bring 112 GiB,
  and memory above 1 GiB/core is billed as extra cores. Memory per rank ×
  ranks per NUMA domain must stay well under ~16 GB; CP2K prints
  `Estimated peak process memory`.
- **Several nodes:** keep 16×7 per node (`node_count` N). For 456 atoms,
  1 node is cheapest, 2 nodes run at 82% efficiency, and 4 nodes at 57%.
  Use more nodes only when wall time matters. Limit: 64 nodes and 24 h on
  `mpc`; a job over a limit is accepted but never starts.

Tell the user the worst-case cost: nodes × 112 × duration in hours, in
core-hours. A single point of a few hundred atoms takes about 1 minute on
one node (456-atom UiO-66: 58 s at 16×7).

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
