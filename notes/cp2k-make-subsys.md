# Plan: `cp2k_make_subsys` as hyogo's first tool

2026-11-04. Built as option C: 05aec12, 33d891a, 5fc2db1, 6b1d4e5. Live cp2k_kinds on HOKUSAI 2026-11-04: Zr q12, O q6, C q4, H q1, N q5, Cu q11, Ti q12, 0.8 s.

## The snag

`make_subsys.py` (135 lines) does three jobs that now belong to three
repos:

| part | what it is | belongs to |
|---|---|---|
| `valence()`: q and `GTH-PBE-qN` per element, read from the build's `BASIS_MOLOPT` / `GTH_POTENTIALS` | a fact of **the build** on the machine | rookery |
| `stretch_ch()`: move H along C–H to 1.08 Å | a **structure** fix | yagura |
| writing `&CELL`, `&COORD`, `&KIND`, electron count, PERIODIC, warnings | **input writing** for a method | hyogo |

hyogo must not SSH and must not import yagura or rookery, so it can't
simply run the old script on the login node.

## Options

- **A. Run locally with a local CP2K data folder.** hyogo needs the two data
  files on this machine (the Mac has one; other users would download ~MB
  per version). Risk: the local data version differs from the cluster
  build's.
- **B. Keep it in rookery** (`hpc_codes`, run on the login node next to the
  build, as first designed). Simple, exact data, but puts input writing in
  rookery, against the split.
- **C. Split by owner (recommended).**
  1. rookery: `cp2k_kinds(facility, elements, basis="DZVP-MOLOPT-SR-GTH",
     build_id=None) -> {element: {q, basis, potential}}`. Read-only on the
     login node against the registry build's `data_dir` (the `valence()`
     logic, sent like `cp2k_bench_parse`). Tiny output, exact data.
  2. yagura: `structure_stretch_xh(path, out_path, length=1.08,
     pairs=["C-H"]) -> {moved, out}`. Local.
  3. hyogo: `cp2k_make_subsys(xyz_path, kinds, out_path="subsys.inc",
     periodic="XYZ", charge=0) -> {out, formula, natoms, electrons,
     widths, warnings}`. Pure local text; `kinds` is step 1's result.
     The agent then `fs_upload`s the file.

  Agent flow: `structure_check` → (`structure_stretch_xh` if flagged
  `short_xh`) → `cp2k_kinds` → `cp2k_make_subsys` → `fs_upload`.

## With C, the work

- **hyogo staged package** (like yagura): `hyogo/` with its own SDK import
  point and server `hyogo-mcp`, `hyogo/cp2k/subsys.py`. Boundary test
  extended: hyogo imports nothing from rookery or yagura and vice versa.
  A ~10-line extended-xyz reader is duplicated in hyogo on purpose: the
  repos meet through tools, not imports.
- `make_subsys.py` stays as the by-hand fallback (login node, one
  command), unchanged; `make_cp2k_md_input.py` (benchmark inputs, rookery)
  keeps its copies until `cp2k_bench_input`, which will call `cp2k_kinds`.
- Plugin: register `hyogo` (fifth server); `structure-making/cp2k/cp2k.md`
  and the three run skills' prerequisites use the flow above.
- Tests: `cp2k_kinds` against a small fake data folder (fixture: a few
  lines of each file), patched `run_command`, configuration-first;
  `structure_stretch_xh` on the flagged fixture; `cp2k_make_subsys`
  output matches `make_subsys.py` byte-for-byte on the same input and
  kinds; empty-HOME starts; boundary.
- Commits: (1) `cp2k_kinds` in rookery; (2) `structure_stretch_xh` in
  yagura; (3) hyogo package + `cp2k_make_subsys`; (4) plugin + skills.
- Live: one `cp2k_kinds` call on HOKUSAI (a few KB).
