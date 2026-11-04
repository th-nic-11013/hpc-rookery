# Building in parallel: proposed sessions

2026-11-04. Each is its own Claude Code session in its own git worktree,
branched from `hokusai-probed-facts` at `2e671c8`, so they cannot
overwrite each other's files. Each commits to its own branch, never
pushes; this session merges the branches back (with your OK), resolves
the overlaps below, re-renders, and pushes to the fork.

## The pieces (independent of each other)

| # | session | package | touches | done when |
|---|---|---|---|---|
| 1 | `cp2k_bench_input`: the benchmark input writer as a tool, using `cp2k_kinds`; retires the last copy of `read_extxyz`/`valence`/`stretch_ch` in `make_cp2k_md_input.py` | rookery `hpc_codes/cp2k/` | benchmark skill, tests | tool + offline test; output equals the script's |
| 2 | the dry-run tool, then `cp2k_bench_records` (parsed benchmark stages → `benchmarks` records) | rookery `hpc_codes/cp2k/` (new modules) | benchmark skill, tests | both tools + offline tests |
| 3 | trajectory analysis in yagura: RDF, MSD, VDOS from an extended-xyz trajectory (numpy only) | `yagura/analysis/` (new) | yagura server, tests | three tools, checked against analytic cases |
| 4 | the AGENTS.md P1: the `fs_rm` refusal test runs against the real local filesystem; point it at an unreachable host or patch `run_command` | `tests/conformance.py` only | — | test can no longer delete local files, still catches a regression |

Not parallel: anything in `hpc_agent_core/` (the scp-fallback engine bug)
waits, because it touches every facility and needs care.

## Where they overlap, and the rules that keep it cheap

- `tests/conformance.py`: every session adds a check. Rule: add new check
  functions at the end of the section, don't reformat others. Merge
  conflicts there are mechanical.
- The tool-list assertions (hpc-codes and yagura server starts): two
  sessions change the same line. I resolve at merge.
- Versions and `plugins/`: sessions **do not bump versions and do not
  commit `plugins/`**; I re-render and bump once after merging.
- Each session runs conformance (normal and empty HOME) and
  `git diff --check` before its final commit, and reports its branch.
- No cluster traffic except piece 2 may need one small live check;
  it asks first.

## Cost

Four sessions run at once, each with its own context: roughly four times
the token use of doing them one by one here, in exchange for wall time.
