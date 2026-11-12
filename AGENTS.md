# AGENTS.md

Read [`PORTING.md`](PORTING.md) before making changes here — it's the
canonical guide for onboarding a new facility. This file covers design
rules and repo facts that apply across the whole repo, not the porting
procedure itself.

## What this repo is

One shared MCP-owning plugin (`plugins/hpc/`) plus one skill-only plugin per
facility (`plugins/hpc-<slug>/`), one MCP server process, every onboarded
facility reachable through one generic, facility-parametrized tool surface.

- The vendored `hpc_agent_core` is **not** API-compatible with PyPI's
  `hpc-agent-core` (every entry point takes `facility`): re-derive a change,
  never copy it across.
- Installing this repo shadows PyPI's `hpc-agent-core` in the same venv.

History and the per-machine repos: [`docs/history.md`](docs/history.md).

## Design rules

- **Every tool takes an explicit `facility` slug as its first argument.**
  There is no default facility, no "current" facility, no global state
  naming one. An agent must always know or discover (`get_facilities()`)
  which facility it's calling.
- **A facility PR touches exactly one directory: `facilities/<slug>/`.**
  See PORTING.md §11 for the full list of what a facility PR does *not*
  need to touch. If a change seems to require editing `hpc_agent_core/` or
  `hpc_mcp/`, that's a signal to reconsider the approach, not a green light
  to edit them — those files serve every facility at once.
- **Clarity over cleverness.** A little facility-specific code in one
  `facility.py` is fine — don't force every dialect difference through a
  maximally generic mechanism if a straightforward subclass override reads
  better (see `facilities/rccs_cloud/facility.py`'s `CloudSlurmBackend` for
  the pattern).
- **Agent scratch goes under `~/agent/`, never the home-directory root.**
  `~/agent/jobs/` already holds rendered job scripts (visible-directory
  bias: agent artifacts live somewhere the user can audit). Uploads, source
  trees, and run directories extend the same idea — they belong under
  `~/agent/work/`. The skill templates tell agents this; tool behavior stays
  path-explicit and doesn't silently reroot.
- **The MCP server must never fail to start.** Per-facility config is read
  lazily, at tool-call time, never at import time.
- **Never write to stdout in server code** — MCP stdio transport uses it
  for JSON-RPC; log to stderr only. `remotemanager`'s progress output is
  redirected by `middleware.py`.

## This repo must never publish `hpc_agent_core` to PyPI

No publish workflow here, ever; CI keeps only `contents: write` (no
`id-token: write`). If published deliberately, it goes out as
`hpc-rookery`, never `hpc-agent-core`. Why: [`docs/no-pypi.md`](docs/no-pypi.md).

## Repo map

```
hpc_agent_core/        The generic engine (SSH middleware, models, scheduler
                        backends, docs RAG, doctor, serving, the facility
                        registry in config.py).
facilities/             One directory per onboarded facility, plus
                        registry.py (facility slug -> SchedulerBackend).
                        Each facility's skill_notes/ holds its real,
                        hand-written skill content (see PORTING.md §7).
hpc_mcp/                The unified server entry points; importing this
                        package registers every facility.
plugins/hpc/            The base Codex plugin: .mcp.json and only the
                        shared hpc-facilities discovery skill.
plugins/hpc-<slug>/     Generated skill-only Codex plugin for one facility.
templates/skills/       Shared per-workflow skill templates (the mechanics
                        every facility has identically) — not per-facility.
codes/                  Code packs: facility-neutral skills per simulation
                        code and stage (codes/cp2k/<stage>/), plus
                        codes/common/ (records, structure-making). Rendered
                        for facilities that opt in via
                        facilities/<slug>/codes/<code>/values.json.
hpc_codes/              The code-pack tools, served by hpc-mcp: records_*
                        tools over the records adapter (records.py) and
                        schemas/records.yaml, the provisional schema of
                        measured records (benchmarks, builds), the cp2k_*
                        tools and launch_plan, in mcp_tools/<group>.py
                        (decorated @_tool). hpc_server.py registers them
                        (and the docs tools) each inside its own guard, so a
                        code tool can't stop the scheduler tools from
                        starting.
yagura/                Structure checking and analysis (trajectories,
                        and GROMACS runs in yagura/gromacs/), its own MCP
                        server (yagura-mcp); tools in mcp_tools/<group>.py,
                        which server.py loads. Staged
                        here before it becomes its own repository: it
                        imports nothing from the packages above and they
                        import nothing from it (conformance enforces it).
hyogo/                 Running simulation methods and writing their
                        inputs (CP2K, VASP; GROMACS build and local
                        equilibration in hyogo/gromacs/), its own MCP
                        server (hyogo-mcp); tools in mcp_tools/<code>.py,
                        each with TOOL_META (its INVENTORY.md rows). Staged
                        like yagura; the three
                        groups import nothing from each other, so the few
                        GROMACS helpers both need are deliberate copies.
scripts/                check (every required check), render_facility_tables.py,
                        render_skills.py, render_plugins.py (--bump: once per
                        merge), render_inventory.py (hyogo/INVENTORY.md table),
                        render_glossary.py (GLOSSARY.md from glossary/).
glossary/               One file per GLOSSARY.md term.
tests/conformance.py    The offline checks' runner (--only, --list,
                        --empty-home); each check is its own file,
                        tests/checks/<area>/<name>.py (facility/ = per facility).
tests/live_smoke.py     Live, facility-agnostic smoke test.
```

## Facilities onboarded so far

Tables (live-tested and awaiting validation), generated:
[`docs/facilities.md`](docs/facilities.md).

## Checks and token hygiene

Run `python3 scripts/check` before committing: it runs every required check (conformance
and the notebook client, each also with `--empty-home`; the five generators with `--check`;
the wheel-data check; `git diff --check`) and prints only failures plus one summary line.
`--only conformance` (or another check name) runs a subset.

Gaps run in parallel, so a gap adds files rather than editing shared ones: a check is a new
`tests/checks/<area>/<name>.py`; a tool goes in its group's `mcp_tools/<group>.py` (a new code
is a new module) with its `TOOL_META` row in hyogo; a term is a new `glossary/<term>.md`.
Generated files (`.gitattributes` lists them) are re-rendered, never edited or hand-merged.
Gap branches never bump plugin versions: `render_plugins.py --bump` does, once per merge.

Keep tool output small: read big files with offset/limit or grep, never print a full
`git diff` unasked (`--stat` instead). The PreToolUse output guard in
`.claude/settings.json` refuses whole-file reads and unbounded diffs/cats of large files;
put `# allow-big` in a command when the user asked to see everything.

## Outstanding code-review work

The backlog carried over from `unified-hub` (P1-P3 items, mostly non-HOKUSAI facilities) is in
[`docs/review-backlog.md`](docs/review-backlog.md). Read it only when working on one of its items.

## Tool coverage

See [`IRI_CHECKLIST.md`](IRI_CHECKLIST.md) — one checklist for the whole
repo (not per-facility), since every facility shares the same tool set.
