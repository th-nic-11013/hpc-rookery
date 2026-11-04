# Plan: stage yagura, with `structure_check` as its first tool

2026-11-04. Built: 5c80be6 (package, tool), plus the plugin/skills commit after it. See `repo-split.md` for why yagura exists.

## Shape

A top-level package `yagura/` in this repo, its own MCP server
(`yagura-mcp`), split into its own repo later.

- Tools take **paths, not facilities**: yagura never reaches a cluster.
  A structure on the cluster is fetched first with rookery's
  `fs_download` (structures are small).
- Depends on numpy only (already a dependency). ASE / pymatgen / spglib
  come later as an optional extra, when the CIF tools move.
- **Boundary test** (conformance): no `yagura/*.py` imports
  `hpc_agent_core`, `hpc_mcp` or `hpc_codes`, and none of those imports
  `yagura`. This is what makes the later split a `git filter-repo`.

## Commit 1: package, tool, tests

- `git mv codes/common/structure-making/scripts/geomcheck.py yagura/geomcheck.py`,
  a symlink back at the old path so the skill still ships the script
  (same pattern as `parse_bench.py`).
- Split it into `check(text) -> dict` and a `main` that prints the same
  text report as today, plus `--json`. Still standard library + numpy,
  still runnable on a login node by hand.
- `yagura/server.py`:
  `structure_check(path) -> {natoms, composition, cell: {lengths, angles,
  volume, widths}, min_pair_distances: [{pair, d, count}],
  flags: [{pair, d, kind}], ok}`; kind is `overlap` (< 0.7 Å) or
  `short_xh` (C–H / N–H < 1.0 Å). Refuses a non-extended xyz with the
  same message the script gives.
- `pyproject.toml`: `yagura-mcp = "yagura.server:main"`, `yagura*` in
  packages.
- Fixture `tests/fixtures/structures/flagged.xyz`: a few atoms in a small
  cell with one overlap and one short C–H; and a clean one.
- Conformance: the fixture results, the empty-HOME start (`yagura-mcp`
  lists `structure_check`), the boundary test.

## Commit 2: register and use it

- `render_plugins.portable_mcp` and `.mcp.json` gain a `yagura` server
  (generated, not hand-edited); the existing "two files match" check
  covers it. Base plugin version bump.
- `structure-making` skill: "run `geomcheck.py`" becomes "call
  `structure_check` (for a file on the cluster, `fs_download` it first)";
  the script stays documented as the manual fallback on the login node.
  `geo-opt`'s two mentions likewise. Re-render, hpc-hokusai 0.10.0.
- README / AGENTS.md repo map: one line for `yagura/`.

## Not in this step

- Moving the `structure-making` skill itself into yagura (it is rendered
  per facility today; making it facility-neutral is its own step).
- `inspect_cif.py` / `to_extxyz.py` (ASE), the shared `read_extxyz`
  helpers, analysis tools.

## Verification

Conformance (normal and empty HOME), notebook client, three generator
checks, wheel-data check, `git diff --check`, privacy grep. One live
call through an MCP client on a local file; no cluster traffic.
