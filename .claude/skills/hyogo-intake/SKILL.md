---
name: hyogo-intake
description: Adapt an existing skill, script, template or snippet into MCP tools in hyogo's inventory - split it by owner (hyogo vs rookery vs yagura vs prose), categorize each tool by method, stage and code, write a plan note, then build the tool with tests and wire it back into the skills. Use whenever the user wants to turn a skill or code into hyogo tools, "toolify" a recipe, add something to hyogo, move run/input logic out of codes/ into hyogo, or fix a hyogo gap logged in notes/ - even if they only say "make this a tool".
---

# Adapting a skill or code into hyogo tools

The inventory and its categories are in `hyogo/INVENTORY.md`. Read it
first; this skill does not repeat it. `cp2k_make_subsys` is the worked
example of the whole procedure: one 135-line script split into a rookery
tool (`cp2k_kinds`), a yagura tool (`structure_check`) and a hyogo tool.

`notes/` is the user's personal, gitignored folder of plans and gap
lists. It may be empty or missing in a fresh clone; that's fine.

## 1. Read the source and the inventory

- Read the whole source: the SKILL.md and every script, template and
  reference it points at. A skill's `Contract` and step list show which
  steps are deterministic.
- Read `hyogo/INVENTORY.md` and `hyogo/server.py`. Search `notes/` for
  gaps already logged against the source (`grep -rn "gap G" notes/`).

## 2. Split it into units

Cut the source into the smallest pieces that each do one job. For each
unit, decide in this order and stop at the first that fits:

1. **Prose.** Judgment, approvals, cost warnings, explanations: these stay
   in the skill.
2. **Another owner.** Use the routing table in INVENTORY.md. A unit that
   needs a cluster, a build or a layout is rookery's; a structure or a
   physics analysis is yagura's. List these units in the plan with their
   owner; build them only if the user says so, each in its own folder and
   commit.
3. **An existing hyogo tool.** If a live tool does most of it, extend that
   tool (a new argument) instead of adding a near-duplicate.
4. **A new hyogo tool.** Only for deterministic work that is input
   writing, method checks, restarts, run-control parsing or selection.

A unit that needs a fact from another server (kinds, widths, a launch
plan) takes it as an argument. hyogo never fetches it.

## 3. Categorize and name each new tool

Give it one `method`, one `stage` and one `code` from INVENTORY.md. If no
category fits, stop and propose a new one to the user: don't stretch an
existing one. The name and module follow from the categories
(`<code>_<verb>_<object>`, `hyogo/<code>/<module>.py`).

## 4. Write the plan, then ask

Write `notes/<tool-or-topic>.md` (local, not committed): the split table (unit, what it is, owner),
the tool signatures with their returns, the agent flow across servers,
the tests, and the commits (one per owning folder). Add the rows to
INVENTORY.md as `planned`, with the plan's content summarised in the
`notes` column (the note itself stays local). Point the user at the note and wait for a yes
before writing code.

## 5. Build

Logic and wrapper are separate:

- `hyogo/<code>/<module>.py`: pure functions on text and dicts. No MCP,
  no file I/O beyond what's unavoidable, no network.
- `hyogo/server.py`: a thin `@mcp.tool()` wrapper that resolves paths,
  refuses bad input, calls the logic and writes the file.

hyogo's rules (conformance enforces the first):

- import nothing from `hpc_agent_core`, `hpc_mcp`, `hpc_codes`,
  `facilities` or `yagura`. Copy a small helper instead, with a comment
  saying it is a deliberate copy;
- no `facility` argument and no SSH: local paths in, local files out. The
  agent moves files with rookery's `fs_upload` / `fs_download`;
- never write to stdout (stdio transport);
- writes take `out_path` (default beside the input) and `overwrite=False`;
- refuse with `ValueError` messages that name the tool to call next
  ("download it first (fs_download)", "pass cp2k_kinds' result");
- return a dict with `out` and a `warnings` list the agent reads to the
  user.

Docstring, as in `cp2k_make_subsys`: what it writes and where it goes;
"Before it:" the tools to call first; "Returns" the keys; "Args".

## 6. Test

Add a check as its own file, `tests/checks/hyogo/<name>.py`, like
`hyogo_make_subsys_matches_the_script_and_stays_separate.py`: a `LABEL` and a
function `<name>()`. The runner finds it; nothing else is edited. Run it alone
with `tests/conformance.py --only <name>`:

- the tool's output matches the source's on the same input: byte for byte
  for a script, apart from a provenance comment. Templates full of
  commented-out alternatives are compared after normalising (drop
  comments and blank lines, expand `@SET`). Name every remaining
  difference in the test;
- copy sources that live outside the repo (scratchpads, `/tmp`) into
  `tests/fixtures/` with a README saying where they came from;
- if the code has a local parse check (e.g. `cp2k --check`), run it on
  every combination the tool writes. Never run a real calculation locally;
- each refusal fires with its message;
- fixtures go in `tests/fixtures/`.

The server-start probe in that check compares the served tools with the
`live` rows of INVENTORY.md, so set the new rows to `live` now.

## 7. Wire it back

- Change the source skill's `Contract` "Tools:" line and the steps to call
  the tool. Keep a by-hand script as the documented fallback if the skill
  had one; delete a template only when the tool fully replaces it.
- Re-render: `scripts/render_skills.py` and `scripts/render_plugins.py`
  (and check `git status` for the regenerated plugin skills).
- Add new terms to `GLOSSARY.md`.

## 8. Verify and hand over

```bash
.venv/bin/python tests/conformance.py
git diff --check
```

The local `hyogo` MCP server keeps the old code until it restarts; say so.
Then report: the tools added (with category), units routed elsewhere and
not built, and the commits proposed (one per folder). Don't commit until
the user asks.
