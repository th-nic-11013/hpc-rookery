---
name: {{SLUG}}-vasp-single-point
description: Run a VASP single point (static energy, forces, stress, per-site magnetic moments, PDOS) on {{DISPLAY_NAME}} - write the INCAR with MAGMOM, NBANDS, NCORE/KPAR and DFT+U checked, submit with the measured layout, decide whether the run really converged, and find frames that landed in the wrong spin state. Use whenever the user wants a VASP energy or forces for a fixed geometry, relabels or labels frames for an MLIP training set with VASP, compares a VASP run with earlier labels, asks why a VASP run or its magnetism looks wrong, or reads per-site moments or a PDOS from a VASP run on {{DISPLAY_NAME}}.
---

# VASP single point on {{DISPLAY_NAME}}

Every `hpc` tool call uses `facility="{{SLUG}}"`. The hyogo and yagura
tools work on files on this computer: `fs_download` first, `fs_upload`
after. Show the user the INCAR's key settings, the job spec and the cost,
and wait for permission before submitting.

## Contract

- **Reads:** `software_registry` (the VASP build) and `benchmarks` (the
  layout), through `launch_plan` and `records_query`.
- **Writes:** the run folder on {{DISPLAY_NAME}}; locally, the INCAR and
  the parsed results (`sites.extxyz`, the environment and comparison TSVs).
  No record: a VASP single point's time is set by its SCF step count, which
  varies from frame to frame, so timings come from `{{SLUG}}-vasp-benchmark`.
- **Tools:** `vasp_make_incar`, `vasp_check_incar`, `vasp_parse_output`
  (hyogo); `sites_by_environment`, `labels_compare`, `pdos_read` (yagura);
  `launch_plan`, `records_query`, `fs_mkdir`, `fs_upload`, `fs_cp`,
  `submit_job`, `get_job_status`, `fs_tail`, `fs_download` (hpc).
- **Preconditions:** the prerequisites below, and the user's approval of
  the INCAR and the cost.
- **Done when:** `vasp_parse_output` says `converged` and the energy,
  forces and (if magnetic) the spin state are reported, or the failure is
  reported with no number quoted.

**Prerequisites:**
- **A VASP build** in `software_registry`. VASP is licensed, so the user
  installs it; record it as `{{SLUG}}-records` says (`variant.exe` is
  `std`, `gam` or `ncl`). Unless the user names one, take the default:
  `records_query(facility="{{SLUG}}", record_type="software_registry", filters={"code": "vasp", "default": true}, fields=["env_script", "version", "variant", "data_dir"])`.
  The binary is `vasp_<variant.exe>`; `data_dir`, when set, is where the
  user keeps the POTCAR library.
- **POSCAR** with the species line (VASP 5 format). Its species order rules
  everything below.
- **POTCAR**: the user's PAW files concatenated **in the POSCAR's species
  order**. Build it in the run folder on {{DISPLAY_NAME}} (a `cat` through
  `{{SLUG}}-remote-command`, with the user's permission), then
  `fs_download` it once so `vasp_make_incar` can check the order
  (`potcar_path`; it reads only the TITEL lines).
- **KPOINTS**, or `KSPACING` in the INCAR. `vasp_gam` needs a
  Gamma-only mesh; KPAR can't exceed the irreducible k-points.
- **A base INCAR.** When the run must match earlier labels or a reference
  run, `fs_download` that run's INCAR and OUTCAR and start from them: every
  physics setting stays, only what you change on purpose changes. Otherwise
  start from `templates/INCAR` and set ENCUT, ISMEAR/SIGMA and the spin with
  the user.

## 1. Write the INCAR

Never type MAGMOM, NBANDS, NCORE or the LDAU lists by hand. One call writes
and checks them:

```text
vasp_make_incar(poscar_path="<local>/POSCAR", base_incar="<local>/INCAR.base",
                magmom={"Cr": 3.0}, parallel={"ranks": <nodes x R>, "kpar": 1, "ncore": 12},
                nbands_from="<local>/reference/OUTCAR", potcar_path="<local>/POTCAR",
                vasp_version="<the build's version>", out_path="<local>/run/INCAR")
```

It writes the INCAR (comments and layout of the base kept) and returns
`magmom`, `total_magmom`, `nbands`, `band_groups`, `changed` and
`warnings`; read the warnings to the user. What it enforces, and why:

- **MAGMOM follows the POSCAR's species order**, one entry per atom; a
  MAGMOM that counts the wrong number of atoms, or comes with `ISPIN = 1`,
  is refused. `magmom` is per element (others get 0); `magmom_sites`
  (`{"17": -3.0}`, 1-based) overrides single atoms, e.g. one flipped site.
  A POTCAR in another order than the POSCAR silently assigns moments and U
  values to the wrong element: pass `potcar_path`.
- **NBANDS is rounded up to a multiple of the band groups**
  (ranks / (KPAR x NCORE)), so a new NCORE, KPAR or rank count can change
  NBANDS and with it the energy. When a run is compared with labels or a
  reference, pin NBANDS: `nbands_from=<that run's OUTCAR>` (NUL holes are
  stripped) or `nbands=<n>`. With `parallel.ranks` given, a pinned NBANDS
  that the band groups don't divide is refused; pick another NCORE.
- **Per-site moments need `LORBIT` >= 10.** Without it the OUTCAR has only
  the total moment, and `sites_out`, `sites_by_environment` and `pdos_read`
  have nothing to read. Set `settings={"LORBIT": 11}` for any spin-polarised
  run whose moments matter.
- **PDOS:** `LORBIT = 11` also puts the projections in `vasprun.xml`.
  `pdos_read` needs an energy grid finer than a fifth of its window
  (`window_ev`, default 0.5 eV): VASP's default NEDOS (301 points over the
  whole eigenvalue range) is too coarse, so set `NEDOS = 3001` or more.
- **DFT+U:** `ldau="mp"` (Materials Project U values) or `{"Cr": 3.7}`
  writes LDAUL/LDAUU/LDAUJ per POSCAR species block and LMAXMIX. Ask
  first: U changes the Hamiltonian, so +U labels can't be mixed with plain
  labels or with E0s computed without U.
- **Layout:** `parallel` writes NCORE and KPAR. Take them from the
  benchmark record that `launch_plan` chose (step 2), so call `launch_plan`
  before writing the INCAR.
- **Spin-state control:** `nupdown` fixes the total moment (right for a
  half-metal or a non-magnetic system, a bias for a metallic magnet);
  `pre` writes a constrained pre-run (section 4).
- Every tag is checked against the VASP documentation when lit-search is
  set up (`incar_check`); a hand-edited INCAR gets the same check from
  `vasp_check_incar(incar_path=..., vasp_version=...)`.

Ask the user before changing anything that changes the physics: ENCUT,
PREC, EDIFF, ISMEAR/SIGMA, LREAL, the functional, the POTCAR set, U, and
ISPIN. For labels the rule is one Hamiltonian for the whole set.

## 2. Stage and submit

```text
~/agent/work/vasp-runs/<system>/sp/   INCAR  POSCAR  KPOINTS  POTCAR
```

`fs_mkdir` the folder, `fs_upload` INCAR, POSCAR and KPOINTS, and build the
POTCAR there (Prerequisites). Get the absolute home path once
(`echo $HOME`); `directory` is passed to `sbatch --chdir`, which doesn't
expand `~`.

Ask rookery for the launch, never write it by hand:

```text
launch_plan(facility="{{SLUG}}", code="vasp", run_type="single_point",
            system={"label": "<system>", "atoms": <atoms>},
            method={"functional": "PBE"},
            build_id=<only if the user named a build>)
```

- `basis.match` `"exact"` or `"similar"`: the layout is the fastest
  recorded one. Its record holds the INCAR side of the layout:
  `records_query(facility="{{SLUG}}", record_type="benchmarks", filters={"id": "<basis.record_ids[0]>"}, fields=["layout", "extra", "metric"])`.
  Pass its `layout.KPAR` and `layout.NCORE` to `vasp_make_incar`'s
  `parallel`, with `ranks` = nodes x R.
- `"none"`: the pack default `{{VASP_DEFAULT_LAYOUT}}` with no estimate.
  Choose NCORE with the user (a divisor of the ranks per node, often
  between 4 and the cores of one socket) and suggest
  `{{SLUG}}-vasp-benchmark` before many runs.
- The estimate is for the benchmark's fixed SCF step count
  (`extra.scf_steps` of the record): scale it, wall ~ `metric.value` /
  `extra.scf_steps` x the SCF steps this system needs. Magnetic and
  slow-mixing systems can take 5x the steps of a simple one.

Submit `launch_plan`'s `job_spec` (`pre_launch`, `launcher`,
`environment`, `resources`, `attributes.queue_name`) unchanged, with these
added:

```json
{
  "name": "vasp-sp-<system>",
  "directory": "/home/<user>/agent/work/vasp-runs/<system>/sp",
  "executable": "vasp_std",
  "post_launch": "gzip -f vasprun.xml",
  "attributes": {"queue_name": "<from job_spec>", "duration": "<scaled estimate x 1.5>"}
}
```

- `vasp_std` is `vasp_<variant.exe>` of the build: `vasp_gam` for a
  Gamma-only mesh (faster), `vasp_ncl` for spin-orbit or non-collinear runs.
- `post_launch` keeps the folder small: a LORBIT 11 `vasprun.xml` is large,
  and `pdos_read` reads it gzipped. Drop it when there is no vasprun to keep.
- Leave `WAVECAR` off (`LWAVE = .FALSE.`) unless a follow-up run reads it.

Tell the user the worst-case cost: nodes x {{VASP_CORES_PER_NODE}} x
duration in hours, in core-hours.

### On {{DISPLAY_NAME}}

{{CODE_NOTES}}

If no notes appear above, the facility has none for VASP yet: check
`search_docs(facility="{{SLUG}}", query="VASP")` for module lines and
licensing rules before the first run.

## 3. Check the result

When `get_job_status` says completed (or failed), `fs_download` the
`OUTCAR`, `OSZICAR` and `POSCAR` into one local folder (the parser reads
the other two when they sit beside the OUTCAR), then:

```text
vasp_parse_output(outcar_path="<local>/OUTCAR", sites_out="<local>/sites.extxyz")
```

(`sites_out` only with LORBIT >= 10.) **"Converged" needs all three of:**
- every ionic step's SCF ended before NELM: `scf.nelm_hit` is 0 (VASP's
  "EDIFF is reached" line per step; fewer than NELM steps on versions
  without it);
- an energy: `energy.toten` (the OUTCAR's TOTEN; the OSZICAR's `F=` line is
  `oszicar.F`);
- a complete force block, one row per atom: `forces.complete`.

`converged` is true only when all three hold. **VASP exits 0 when the SCF
hits NELM** and still prints an energy and forces; a job state of
"completed" says nothing. With `converged` false there is **no usable
energy**: report it and quote no number.

Read every warning to the user. The ones that matter most:
- **NUL bytes** (`nul_bytes` > 0): holes left in the OUTCAR by a full
  disk. The parser strips them and reads around them; trust only
  `converged` and `forces.complete`, and pass `poscar_path` if the header
  was lost.
- An OSZICAR from another run, a cut file, VASP's own warning boxes
  (`messages`).

Then report: `energy` (eV; `per_atom_toten`), `forces.max` and `rms`
(eV/A), `stress_kb`, `e_fermi`, `scf.steps`, `cost.elapsed_s`, and for
ISPIN 2 `magnetization.total` and `magnetization.sites.by_element`
(`opposed`: sites pointing against their element's net moment). Check
`params.NBANDS` against the pinned value and `layout` against what was
planned.

- **Where the moments sit:**
  `sites_by_environment(path="<local>/sites.extxyz")` classes every atom
  (metal, `water_O`, `slab_O`, `other_O`, ...) and sums the moments and
  charges by class; `metal_spin` lists antiparallel metal sites. `metals`
  overrides the metal list.
- **PDOS:** `fs_download` `vasprun.xml.gz` and call
  `pdos_read(paths=["<local>/vasprun.xml.gz"], group_by="environment")`.
  Per spin and group it gives the states just below and above E_F and
  `edge_ev`, the top of each group's occupied band. Read its warnings: a
  grid coarser than `window_ev / 5` means NEDOS was too small. A vasprun
  cut by a full disk may not parse; the OUTCAR still has the energy,
  forces and moments.
- **Against earlier labels or a reference run:** both as extxyz (each run's
  `sites_out`, or the label set), then
  `labels_compare(a_path="<reference>.extxyz", b_path="<new>.extxyz")`.
  Frames are paired by geometry; a pair is flagged for a force RMS
  difference above 50 meV/A, an energy shift far from the median, or a site
  moment that changed sign.

## 4. Spin-state traps (magnetic systems)

A magnetic system has several self-consistent spin states, and a run can
converge to any of them. On a magnetic slab under water (an M2XO2 MXene
labelling campaign) this showed up as:
- **flipped metal moments:** one or more metal sites antiparallel to the
  rest. The total moment changes in steps (a half-metal's total is an
  integer: 60 uB all parallel, 54 uB with one site flipped), and the energy
  by only about 1 meV/atom, but the forces by 200-280 meV/A RMS. For MLIP
  labels, spin consistency matters more for the forces than for the
  energies;
- **a stray moment on water O:** when the top of the water-O band reaches
  the slab's Fermi level (a semilocal-functional artefact), charge sloshes
  between slab and water, a fractional moment sits on water oxygens, and
  the SCF needs 150-200+ steps or never converges.

Find them, don't guess:
1. `vasp_parse_output`: `magnetization.sites.by_element[el].opposed` > 0 or
   a `largest` site that isn't a metal.
2. `sites_by_environment` on `sites.extxyz`: `classes.water_O` with a
   non-zero `sum_abs` moment, or `metal_spin[el].antiparallel` > 0.
3. `pdos_read(..., group_by="environment")`: a `water_O` `edge_ev` at or
   above about 0 eV, i.e. occupied water states at E_F.
4. Across a label set, `labels_compare` against a reference run of the
   same frames: `flipped_sites` and the force-RMS flags name the frames in
   another state. Drop or relabel them.

To steer a frame into the intended state, **constrain, then release**:

```text
vasp_make_incar(poscar_path=..., base_incar=..., magmom={"Cr": 3.0},
                pre={"nupdown": 60}, nbands=<pinned>, parallel={...}, out_path="<local>/run/INCAR")
```

It also writes `INCAR.pre`: the same INCAR with NUPDOWN fixed, a loose
EDIFF (1E-4), its own NELM (120) and `LWAVE = .TRUE.`; the main INCAR gets
`ISTART = 1` and no NUPDOWN.
1. Run `INCAR.pre` (renamed `INCAR`) in `<run>/pre/` with the same POSCAR,
   KPOINTS and POTCAR.
2. `fs_cp` `pre/WAVECAR` into `<run>/` and run the main INCAR there: it
   starts from the constrained wavefunction with the moment free.
3. Check the result as in step 3: the released run may still drift to
   another state, and that is the answer, not an error.

Keep NBANDS and the layout the same in both runs. What did not help in that
campaign: linear mixing (small AMIX/BMIX) converged more slowly, an
all-parallel starting MAGMOM did not stay parallel, and +U changed the
Hamiltonian without fixing the water band. Settle a spin protocol on a few
test frames, then apply it unchanged to the whole set.

## By hand, without the hyogo server

`fs_tail` / `fs_view` the OUTCAR and OSZICAR:

| look for | meaning |
|---|---|
| `General timing and accounting` (end of OUTCAR) | finished |
| `aborting loop because EDIFF is reached` once per ionic step | that step converged |
| `aborting loop EDIFF was not reached`, or the OSZICAR's last `DAV:`/`RMM:` step number equal to NELM | **not converged**: exit 0 anyway, no usable energy |
| `F=` line in the OSZICAR | the ionic step ended (F, E0, mag) |
| `free  energy   TOTEN  =` | the total energy |
| `TOTAL-FORCE (eV/Angst)` block | the forces: one row per atom, or the run was cut |
| `magnetization (x)` block | per-site moments (s, p, d, tot); only with LORBIT >= 10 |
| `distr:  one band on NCORE= ... groups` | NCORE and the band groups the run used |
| `number of bands    NBANDS=` | the band count actually used |

`grep` refuses or misreads a file with NUL holes ("Binary file matches");
`vasp_parse_output` is the reliable reader.

Common failures:

| symptom | fix |
|---|---|
| `NBANDS` in the OUTCAR differs from the INCAR | VASP rounded it to the band groups: pin a value they divide (`vasp_make_incar` refuses one that doesn't) |
| energies off by eV between "identical" runs | different POTCAR order or set; compare `potcars` from `vasp_parse_output` |
| VASP stops at start on KPAR, or hangs there | KPAR must divide the ranks and not exceed the irreducible k-points (`params.NKPTS` of an earlier run) |
| `vasp_gam` stops on the k-mesh | `vasp_gam` needs a Gamma-only mesh; use `vasp_std` |
| SCF at NELM on a magnetic frame | section 4; don't just raise NELM |
| disk quota exceeded, empty or NUL-filled outputs | gzip vasprun.xml, keep `LWAVE`/`LCHARG` off, and re-run every frame whose run overlapped the full disk |
