---
name: hokusai-cp2k-single-point
description: Run a CP2K single-point energy and forces calculation on HOKUSAI BigWaterfall2 (HBW2) - write the input, parse-check it, submit with the measured MPI x OpenMP layout, and check SCF convergence. Use whenever the user wants an energy, forces, a wavefunction, a gap, or reference data (e.g. MLIP training labels) for a fixed geometry with CP2K on HOKUSAI BigWaterfall2 (HBW2), or a first test that a CP2K setup converges for a new system.
---

# CP2K single point on HOKUSAI BigWaterfall2 (HBW2)

Every tool call uses `facility="hokusai"`. Show the user the input's key
settings and the job spec, state the cost, and wait for permission before
submitting.

## Contract

- **Reads:** `benchmarks` and `software_registry`, through `launch_plan`.
- **Writes:** no records. It writes `single_point.out` and, if asked, the
  wavefunction.
- **Tools:** `cp2k_make_input` (the hyogo server, writes the input),
  `launch_plan` (layout, job spec, cost), `fs_upload`,
  `cp2k_check_input` (parse check),
  `submit_job`, `get_job_status`, `fs_download`, `cp2k_parse_output`
  (the hyogo server, reads the output); `fs_tail` / `fs_view` while it runs.
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
  its `cp2k/cp2k.md`. Keep both results: `cp2k_kinds`' and
  `cp2k_make_subsys`'. The kinds must come from the functional's family
  (see "Functionals" below), so choose the functional first.

## 1. Write the input

```text
cp2k_make_input(run_type="sp", functional="revpbe",
                subsys=<cp2k_make_subsys' result>, kinds=<cp2k_kinds' result>)
```

It writes `single_point.inp` beside `subsys.inc` on this machine (or at
`out_path`), with the tunables as `@SET` lines at the top, and returns
the `settings` and `warnings`. Read the warnings to the user. The default
preset is the one run and benchmarked on HOKUSAI BigWaterfall2 (HBW2):
- revPBE-D3 (zero damping), `DZVP-MOLOPT-SR-GTH` with `GTH-PBE-q<N>`;
- 450/60 Ry, Gamma point;
- OT DIIS with `PRECONDITIONER FULL_ALL` and `ENERGY_GAP 0.05`.

That SCF converged a 456-atom MOF cell from the atomic guess in 55
steps. The older default, `FULL_SINGLE_INVERSE`, stalled on the same cell.

Ask the user about anything that changes the physics:

| setting | default | change when |
|---|---|---|
| charge, `MULTIPLICITY`, `UKS` | from `cp2k_make_subsys` (its `charge`); 1, or 2 for odd electrons | the user says so: `multiplicity=` (UKS follows) |
| functional | `revpbe` (revPBE-D3, zero damping) | the user asks for another: see "Functionals" below. Ask first |
| `CUTOFF` / `REL_CUTOFF` | 450 / 60 Ry (600 for r²SCAN and hybrids) | converge for production: run 350–700 Ry and take the value where the energy changes < 1 meV/atom (and forces < 1e-4 Ha/bohr) |
| SCF | OT DIIS + FULL_ALL | metals and small-gap systems: `scf="smear"` (diagonalisation, Fermi-Dirac 300 K, Broyden) |
| isolated molecule | periodic | follows `subsys.inc`: made with `periodic="NONE"`, the input gets `&POISSON` |
| gap, wavefunction | off | `print_gap=True` (HOMO-LUMO), `save_wfn=True` (`<PROJECT>-RESTART.wfn`) |

The input has **no `IGNORE_CONVERGENCE_FAILURE`**. An unconverged SCF
must stop the run, not produce an energy that looks valid.

### Functionals

Each preset needs its own basis and potential family, so it starts at
`cp2k_kinds`. The tool refuses a mismatch.

| `functional` | `cp2k_kinds(...)` | D3 | notes |
|---|---|---|---|
| `revpbe` | `family="molopt"` (default) | zero | benchmarked on HOKUSAI BigWaterfall2 (HBW2) |
| `pbe` | `"molopt"` or `"uzh-pbe"` | BJ | |
| `r2scan` | `"uzh-scan"` | BJ only | meta-GGA: ~3× the PBE cost per SCF step; converge the cutoff |
| `pbe0` | `"uzh-pbe0"`, `aux_basis="admm-dzp"` | BJ | truncated exact exchange plus long-range correction |
| `hse06` | `"uzh-pbe0"`, `aux_basis="admm-dzp"` | BJ | screened (ω = 0.11 bohr⁻¹); the robust hybrid for small cells |
| `r2scan0` | `"uzh-scan"`, `aux_basis="admm-dzp"` | BJ only | truncated exact exchange, no long-range correction |

For the hybrids (ADMM, 25 % exact exchange):
- `subsys.inc` is made from the `aux_basis` kinds, so it carries the
  `AUX_FIT` lines;
- **the operator stays below half the narrowest cell width** (from
  `cp2k_make_subsys`' `widths`): Γ-point HFX counts periodic images twice
  beyond it. PBE0 and r²SCAN0 truncate just below; HSE06 keeps CP2K's own
  range, where erfc(0.11 r)/r reaches `EPS_SCHWARZ` (14.3 Å at 1e-6), and
  is capped with a warning when that's too far. A larger `hfx={"radius"}`
  is refused: use a supercell. Converge the property against the radius;
- the tool also writes the settings that keep HFX correct, so don't edit
  them out: `EXCH_CORRECTION_FUNC DEFAULT` (the ADMM correction for the HF
  operator in use), `MIN_PAIR_LIST_RADIUS -1`, and `EPS_SCHWARZ_FORCES` equal
  to `EPS_SCHWARZ`;
- **start from a converged GGA wavefunction in the same basis**:
  `save_wfn=True` on a PBE single point, then `guess_wfn="<its
  -RESTART.wfn>"`. From the atomic guess every SCF step costs the full HFX
  price; a 456-atom HSE06 run on HOKUSAI BigWaterfall2 (HBW2) ran out of time that way.
  `screen_on_initial_p` follows: on with a restart, off from scratch;
- expect 5–20× the GGA cost per step. Set `hfx={"memory_mb": ...}` (MiB
  per MPI rank, default 3000) from the node's memory per rank; the output's
  `HFX_MEM_INFO` lines show whether the integrals fit. Run a short test
  job first to measure both;
- convergence tests vary one setting at a time inside one hybrid-matched
  family. The reference for the ADMM basis is a run **without** ADMM: an
  auxiliary basis as large as the primary is outside what ADMM is for;
- `stress=True` adds the analytical stress tensor (GPa), and
  `precision={"eps_pgf_orb": ...}` tightens `&QS`.

**By hand, without the hyogo server:** copy `templates/single_point.inp`
(revPBE-D3 only) next to `subsys.inc` and set its `@SET` lines; its
comments give PBE, smearing and `&POISSON`.

Check the syntax on the login node before any job. A serial parse takes a
few seconds:

```text
cp2k_check_input(facility="hokusai", input_path="<run dir>/single_point.inp")
```

It uses the default build unless you pass `build_id`, and returns
`ok`, the `errors` CP2K printed, and the output's tail. It also lists the
restart and wavefunction `files` the input will read, with `exists`; a
missing wavefunction leaves `ok` true but is in `warnings` (CP2K would start
from the atomic guess), so read `warnings` before submitting.

This proves the syntax, including the `@INCLUDE`, but not that the SCF
converges.

## 2. Stage and submit

```text
~/agent/work/cp2k-runs/<system>/sp/   single_point.inp  subsys.inc
```

Get the absolute home path once (`echo $HOME`); `directory` is passed to
`sbatch --chdir`, which doesn't expand `~`.

Ask rookery for the launch, never write it by hand:

```text
launch_plan(facility="hokusai", code="cp2k", run_type="single_point",
            system={"label": "<system>", "atoms": <atoms>},
            build_id=<only if the user named a build>)
```

Submit its `job_spec` (`pre_launch`, `launcher`, `environment`,
`resources`, `attributes.queue_name`) unchanged, with these added:

```json
{
  "name": "cp2k-sp-<system>",
  "directory": "/home/<user>/agent/work/cp2k-runs/<system>/sp",
  "executable": "cp2k.psmp -i single_point.inp -o single_point.out",
  "attributes": {"queue_name": "<from job_spec>", "duration": "01:00:00"}
}
```

### Layout: R ranks × T threads per node, from benchmark records

`launch_plan` reads the layout from the `single_point` records (never an
`aimd_step` one: the two workloads rank layouts differently). Tell the user
what `basis` says:
- `match: "exact"`: this system was timed; `basis.record_ids` names the
  record.
- `match: "similar"`: a system of about this size; the time is rough.
- `match: "none"`: a common layout with no estimate. Suggest
  `hokusai-cp2k-benchmark` first if many single points will run.

Read `warnings` out too (e.g. the timing came from another build).
`optimize="time"` buys wall time with more nodes, `nodes=<n>` fixes the
count; `basis.options` lists every measured node count with its cost and
parallel efficiency.

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
  `basis.options` from `launch_plan` for the parallel efficiency at
  other node counts. Use more nodes only when wall time matters.

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

When `get_job_status` says completed (or failed), `fs_download` the
`.out` and call `cp2k_parse_output` on it. It returns `finished`, the SCF
per evaluation (`scf.steps`, `scf.not_converged`), `energy_ha`, `gap_ev`,
`forces` (max, RMS, Ha/bohr), `stress_gpa` and `cost` (time, peak and
HFX memory), and `warnings` to read to the user: an unfinished output,
an unconverged SCF, and CP2K's own `*** WARNING` boxes in full.

- `finished` false, or `scf.not_converged` > 0: **no usable energy.**
  Report it; don't quote a number.
- Quote energy, gap, forces and stress from the result, not from memory
  of a grep.
- `cost` is what a benchmark record needs (`hokusai-records`).

By hand, if the hyogo server isn't available: `fs_tail` / `fs_view` on
the `.out`:

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
| an element in `cp2k_kinds`' `errors` (or `no ... for <element>` from `make_subsys.py`), or a basis/potential not found | see `structure-making/cp2k/cp2k.md` |
| `error while loading shared libraries`, exit 127 | the job didn't source `cp2k_env.sh` |
| SCF stalls (OT gradient flat at 1e-5) | check charge and multiplicity. Then try OT CG, or diagonalisation + Broyden (both converged the 456-atom cell) |
| `module: command not found` | the job was submitted with `--export=NONE`. Don't |

Save the wavefunction (`<PROJECT>-RESTART.wfn`) when a follow-up
calculation on the same geometry will need it, and start that from it with
`SCF_GUESS RESTART`.
