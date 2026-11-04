# Plan: the CIF tools move to yagura

2026-11-04. Draft, nothing built.

## What moves

| script today (`codes/common/structure-making/scripts/`) | lines | becomes |
|---|---|---|
| `inspect_cif.py` | 43 | `cif_inspect(path)` |
| `to_extxyz.py` | 57 | `structure_to_extxyz(source_path, out_path, vacuum=None, charge=0, index=-1, overwrite=False)` |

Same pattern as `geomcheck.py`: `git mv` to `yagura/`, a symlink back so
the skill still ships the scripts, each split into a function returning
a dict plus the unchanged printed report.

## Results

- `cif_inspect` → `{cell: {lengths, angles}, space_group, formula_sum, z,
  sites: [{label, element, x, y, z, occupancy, group}], partial: [labels],
  partial_groups: {group: [labels]}}`. It reads; it never repairs.
- `structure_to_extxyz` → `{out, natoms, formula, cell, electrons,
  multiplicities, partial_occupancy_warning}`. Refuses a structure with no
  complete cell (unless `vacuum`), refuses to overwrite unless asked,
  warns when the source is a CIF with partial sites (they come out as
  full atoms). Then the agent runs `structure_check` on `out`.

## ASE: optional, and the server must still start without it

- `pyproject.toml`: `[project.optional-dependencies] yagura = ["ase>=3.23,<4"]`
  (spglib later, when a symmetry tool needs it).
- ASE is imported **inside** the two tools, so `yagura-mcp` starts and
  `structure_check` / `structure_stretch_xh` work without it; the CIF
  tools then say how to install it.
- The plugin's yagura server runs with the extra:
  `uv tool run --from "hpc-rookery[yagura] @ git+...@main" yagura-mcp`
  (only that entry changes, in `render_plugins` and `.mcp.json`).
- CI installs `-e .[yagura]`, so the tests below run there too.

## Tests

- Fixture: a small P1 CIF with one full site and two half-occupied
  alternative positions (disorder group 1/2), and a molecule xyz with no
  cell.
- `cif_inspect` lists the partial sites and groups; the printed report
  equals the old script's output.
- `structure_to_extxyz`: CIF → xyz that `structure_check` reads; the
  no-cell molecule is refused, then boxed with `vacuum=6`; partial-site
  warning present; no overwrite.
- Without ASE (simulated by blocking the import): the server still lists
  all four tools and the CIF tools raise the install message.
- Boundary test unchanged (ASE is third-party, allowed).

## Commits

1. yagura: `cif_inspect`, `structure_to_extxyz`, optional extra, tests,
   CI install line.
2. plugin + skill: the yagura server entry with the extra; the
   structure-making skill's tool table and steps call the tools, scripts
   kept as the by-hand path. Version bumps.

## Cost

Installing ASE into `.venv` downloads ASE plus scipy and matplotlib,
roughly 40–50 MB. No cluster traffic.
