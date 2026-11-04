# End-to-end UiO-66 AIMD through the tools

2026-11-04. The purpose is to exercise every tool on one real HOKUSAI job, and fix whatever breaks.

## System and job

- Input structure: `rccs-agentic-ai/mof-co2-capture/structures/uio66_prim_114.xyz`. This is the primitive cell: 114 atoms, C48H28O32Zr6.
- Method: revPBE-D3 with DZVP-MOLOPT-SR-GTH basis sets, cutoffs 450/60 Ry, NVT with a CSVR thermostat at 300 K, 1000 steps of 0.5 fs.
- Template change: `FULL_SINGLE_INVERSE` instead of the template's `FULL_ALL`, to match the record behind the estimate. `WALLTIME` is 8100 s, which is the 02:30:00 job limit minus 15 min.
- `launch_plan`:
  - the match is exact, with record `...-uio66prim-aimdstep-1n-16x7`;
  - 1 `mpc` node, 16 × 7;
  - 5.14 s/step, about 160 core-hours.
- Run directory: `~/agent/work/cp2k-runs/UiO-66-prim/md/`.

## Steps and what happened

| step | tool | result |
|---|---|---|
| check | `structure_check` | 24 C–H bonds at 0.949 Å flagged as `short_xh` (an X-ray structure). The μ3-OH O–H at 0.998 Å is fine |
| fix | `structure_stretch_xh` | 24 moved to 1.08 Å; the recheck is clean |
| kinds | `cp2k_kinds` | Zr q12, O q6, C q4, H q1 from the 2026.2 data directory |
| subsys | `cp2k_make_subsys` | 484 electrons, periodic XYZ, no warnings |
| stage | `run_command` (mkdir), `fs_upload` ×2 | checksums verified. One earlier attempt failed once with "could not communicate with process" and did not repeat |
| parse | `cp2k_check_input` | ok |
| plan | `launch_plan` | as above |
| submit | `submit_job` with the plan's `job_spec` unchanged | submitted; the rendered script matches the plan (`--nodes` is left out for 1 node by design) |
| download | `fs_download` ×3 | `md-pos-1.xyz`, `md-1.ener` and `aimd_nvt.out` went to the scratchpad by rsync, checksums verified |
| analyse | `trajectory_rdf` / `trajectory_msd` / `trajectory_vdos` with `cell=` the 3×3 lattice | see Results |

## Bugs found

1. `structure_check`'s `composition` keys were `np.str_`. Fixed with `str(e)`. The conformance check now asserts plain types and a JSON round trip.
2. The transient "could not communicate with process" on the first `run_command`/`fs_upload` of a session. Not reproduced; watch for it.
3. `tests/conformance.py`'s records check read the shared benchmarks
   outside its `HPC_RECORDS_USER_DIR` patch, so any user-scope benchmarks
   record failed it. That happened as soon as this run appended one.
   Fixed: the query now runs inside the patch.

## Results (job 9729225, finished 2026-11-04 15:40 JST)

- **Run:** 1000 of 1000 steps, exit 0, elapsed 01:41:08 on bwmpc143. The
  SCF converged on every step, 7.7 SCF steps per MD step on average.
- **One CP2K warning:** `CELL%CANONICALIZE AUTO` keeps the non-lower-triangular
  fcc primitive cell. This is harmless.
- **Conserved quantity:** drifted 2.6e-4 Ha over 0.5 ps, which is
  0.13 meV/atom/ps. That is good.
- **Temperature:** not equilibrated. Steps 0–99 averaged 215 K, and it
  rose slowly to 282 K over steps 800–999 (with `TIMECON` 100). The whole
  run is equilibration; analysis used frames 500 onward.
- **Speed:** 5.80 s/step (mean `UsedTime` over steps ≥ 100) against the
  record's 5.14, about 13 % slower. Appended to the user-scope records as
  `hokusai-cp2k-2026-11-04-uio66prim-aimd1000-1n-16x7`.
- **RDF:**
  - Zr–O first peak at 2.23 Å, coordination number 8.0;
  - C–H at 1.089 Å, 0.5 per C (24 H over 48 C);
  - O–H at 0.969 Å, 0.125 per O (the 4 μ3-OH). The bonding held.
- **MSD:** not meaningful. H has a log-log slope of 1.09, but its MSD is
  only 0.27 Å² over 0.25 ps, which is vibration. Zr plateaus at 0.017 Å².
  No D is reported.
- **VDOS:** peaks at 3189 cm⁻¹ (C–H stretch), 3754 (μ3-OH), 1580
  (carboxylate), 1177 and 842. Resolution is 67 cm⁻¹.
- **Skill fix:** `codes/cp2k/aimd/SKILL.md` now has §6, "Analyse the
  trajectory", which points to the three yagura tools. It covers the cell,
  `dt_fs` and Nyquist, dropping equilibration, and the false-diffusion
  trap. The Temp row has the slow-equilibration data point. Re-rendered.

## Open follow-ups

- A tool improvement: `trajectory_msd` could warn when `loglog_slope` is
  about 1 but the MSD stays below about 1 Å². Today only the skill text
  guards against reading that as diffusion.

## Resume here (for a fresh session)

Steps 1–5 below are done; only step 6 (commit, then ask about pushing) remains.

As of 2026-11-04 ~15:15 JST: job running, step 762 of 1000, 5.8 s/step
(the record says 5.14), temperature 294–304 K, conserved quantity steady
at −1087.9034 Ha.

1. Check it's done: `get_job_status("hokusai", <job id>)`; the ID is in
   `~/.hpc-agent` history or `sacct -n -X --name=cp2k-md-UiO-66-prim`.
2. `fs_download` `md-pos-1.xyz`, `md-1.ener` and `aimd_nvt.out` from
   `~/agent/work/cp2k-runs/UiO-66-prim/md/`.
3. Check the run (the AIMD skill, §5): temperature, conserved-quantity
   drift, SCF steps per MD step, and s/step from `UsedTime`. If the
   s/step differs from the record, append a user-scope benchmarks record.
4. yagura: `trajectory_rdf` (Zr-O, C-H, O-H),
   `trajectory_msd` (H, dt_fs=0.5), `trajectory_vdos` (dt_fs=0.5). Pass
   `cell=` the 3×3 `Lattice` from
   `rccs-agentic-ai/mof-co2-capture/structures/uio66_prim_114.xyz`, line 2.
5. Expected gap: the AIMD skill's "check the run" section doesn't point to
   the yagura analysis tools. Fix it in `codes/cp2k/aimd/SKILL.md`, then
   re-render.
6. Commit as one chunk:
   - the uncommitted `yagura/geomcheck.py` str-key fix plus its
     conformance assertion;
   - the skill fix;
   - `notes/` and `GLOSSARY.md`.

   Then ask about pushing to the fork.
