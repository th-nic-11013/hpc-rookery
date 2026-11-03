---
name: hokusai-cp2k-geo-opt
description: Run a CP2K geometry optimisation (atomic positions, GEO_OPT) or cell optimisation (positions plus lattice, CELL_OPT) on HOKUSAI BigWaterfall2 (HBW2) - write the input, submit, restart when it hits the wall-time limit, and check convergence. Use whenever the user wants to relax, optimise or minimise a structure with CP2K on HOKUSAI BigWaterfall2 (HBW2), including relaxing X-ray hydrogens or a built MOF before AIMD.
---

# CP2K geometry and cell optimisation on HOKUSAI BigWaterfall2 (HBW2)

Every tool call uses `facility="hokusai"`. Show the user the key
settings and the job spec, state the cost, and wait for permission before
submitting.

**Prerequisites:**
- a CP2K build (`~/software/cp2k/cp2k_env.sh` by default; `hokusai-cp2k-build`);
- `subsys.inc` (`hokusai-structure-making`, `cp2k/cp2k.md`);
- **a converged single point on the starting geometry**
  (`hokusai-cp2k-single-point`). It proves the SCF settings work for this
  system before you pay for dozens of steps. On HOKUSAI BigWaterfall2 (HBW2), an
  unconverged SCF once cost 153.6 core-hours for no result; a single point
  would have shown the problem for about 13.

## 1. Choose the run type

| the user wants | template | notes |
|---|---|---|
| relax atoms, fixed cell (molecule, slab, defect, X-ray H) | `templates/geo_opt.inp` | `RUN_TYPE GEO_OPT` |
| relax atoms **and** lattice (bulk, MOF, equation of state) | `templates/cell_opt.inp` | `RUN_TYPE CELL_OPT` with the analytical stress tensor. Default `CUTOFF` 600: stress needs a higher cutoff than energies (Pulay stress), so converge it on the stress |

Both have the single-point template's DFT block (revPBE-D3,
`DZVP-MOLOPT-SR-GTH`, OT DIIS + FULL_ALL) plus ASPC wavefunction
extrapolation between steps. Keep any change made there (functional,
cutoff, smearing, charge, spin) in step here, or the energies won't be
comparable.

Settings to confirm:
- **Optimiser:** `BFGS` up to a few hundred atoms. For larger systems use
  `LBFGS`, since BFGS keeps a dense Hessian. `CG` is a robust fallback
  when BFGS oscillates.
- **Convergence:** `MAX_FORCE 4.5E-4` hartree/bohr (≈ 0.023 eV/Å, CP2K's
  default). Tighten to `1.0E-4` before computing frequencies.
- **Fixed atoms:** the commented `&CONSTRAINT / &FIXED_ATOMS` block, with
  1-based indices in `subsys.inc` order.
- **Cell optimisation:** `KEEP_SYMMETRY` or `KEEP_ANGLES` (commented in the
  template), `EXTERNAL_PRESSURE` 1 bar, `PRESSURE_TOLERANCE` 100 bar. Don't
  add a `TYPE` keyword: recent CP2K removed it (`--check` aborts on it),
  and direct cell optimisation is always used.
- **`WALLTIME`** (seconds) is the job's duration minus ~10 minutes:
  `12:00:00` → `42600`. CP2K then stops cleanly and writes its restart
  file before Slurm kills the job.

Parse-check on the login node before submitting:
`( source ~/software/cp2k/cp2k_env.sh; cd <run dir>; OMP_NUM_THREADS=1 timeout 60 cp2k.psmp --check -i geo_opt.inp )`.
Both templates passed `--check` and ran to completion on CP2K 2026.2.

## 2. Stage and submit

Stage the input and `subsys.inc` in `~/agent/work/cp2k-runs/<system>/opt/`.

```json
{
  "name": "cp2k-opt-<system>",
  "directory": "/home/<user>/agent/work/cp2k-runs/<system>/opt",
  "pre_launch": "source $HOME/software/cp2k/cp2k_env.sh",
  "launcher": "srun --mpi=pmix --cpus-per-task=7 --cpu-bind=cores --distribution=block:block",
  "executable": "cp2k.psmp -i geo_opt.inp -o geo_opt.out",
  "environment": {"OMP_NUM_THREADS": "7", "OMP_PLACES": "cores", "OMP_PROC_BIND": "close"},
  "resources": {"node_count": 1, "processes_per_node": 16, "cpu_cores_per_process": 7},
  "attributes": {"queue_name": "mpc", "duration": "12:00:00"}
}
```

The layout (16 × 7 per node, measured fastest) and the binding rules are
explained in `hokusai-cp2k-single-point`. Use more nodes only for large
cells, with 16 × 7 per node: 456 atoms ran at 82% efficiency on 2 nodes.

Cost estimate. A step after the first costs about one ASPC-extrapolated
SCF: a few seconds for ~100 atoms, ~10 s for 456 atoms on one node.
Typical runs take 50–200 steps. Tell the user the worst case: nodes × 112
× duration in hours.

## 3. Check progress and the result

| in the `.out` | meaning |
|---|---|
| `OPTIMIZATION STEP:` | one per step |
| `OPT\| Total energy [hartree]`, `OPT\| Used time [s]` | energy and time of that step |
| `OPT\| Maximum gradient is converged  YES/NO` (and the same for maximum step size, RMS step size, RMS gradient) | progress on each criterion (2026.2 wording; `grep "is converged"`) |
| `GEOMETRY OPTIMIZATION COMPLETED` | **converged** |
| `MAXIMUM NUMBER OF OPTIMIZATION STEPS REACHED` | not converged. Restart (section 4) |
| ended at `WALLTIME` | not converged. Restart |

Output files (checked on CP2K 2026.2):

| file | holds |
|---|---|
| `<PROJECT>-pos-1.xyz` | the trajectory, one frame per step |
| `<PROJECT>-FINAL-1_<step>.xyz` / `.cif` | the final geometry, written at the end (as of 2026.2) |
| `<PROJECT>-1.restart` | a complete input at the latest geometry, for restarting |
| `<PROJECT>-1.cell` | lattice per step (CELL_OPT) |
| `<PROJECT>-BFGS.Hessian` | the optimiser's Hessian (BFGS) |

Energy rising for several steps in a row, or a gradient that oscillates,
usually means a noisy SCF: tighten `EPS_SCF` to `1.0E-7`, or switch to
`CG`. Before handing the optimised structure to AIMD, download the final
frame and run `geomcheck.py` on it (`hokusai-structure-making`).

## 4. Restart

Copy the input and add, at the top level:

```text
&EXT_RESTART
  RESTART_FILE_NAME <PROJECT>-1.restart
&END EXT_RESTART
```

Also set `SCF_GUESS RESTART`, then submit with a new output name
(`-o geo_opt_2.out`). The optimisation continues from the restart file's
geometry and step counter.

Prefer one longer job to a dependency chain. On HOKUSAI BigWaterfall2 (HBW2),
dependents of a failed `afterok` job are **never** cancelled automatically;
they sit pending with `DependencyNeverSatisfied` until cancelled by ID.
If you do chain jobs, use
`"custom_attributes": {"dependency": "afterok:<id>", "kill-on-invalid-dep": "yes"}`
(the second option is untested here). Check for stuck dependents, and
cancel them by explicit ID with the user's OK.
