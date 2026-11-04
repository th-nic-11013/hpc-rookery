# When to split rookery / hyogo / yagura, and how to work until then

2026-11-04.

## Does "guided runs, updating each repo as we go" make sense?

Yes. It is the cheapest way to find the right tool boundaries: each real
workflow shows which tool is missing, which arguments are wrong, and which
repo owns a fix. The staged folders (`yagura/`, `hyogo/`) are already the
future repos, and conformance enforces that the three import nothing from
each other. So "update the concerned repo" today means "edit the concerned
folder". Each commit stays inside one of them, and the split is mechanical
later.

## Why not split now

The tool interfaces are still moving. The UiO-66 run alone has already
changed `structure_check`'s output. In three repos, every interface change
needs a release in one repo and a version bump in another, plus a plugin
pointing at both. One repo with enforced boundaries gets the same separation
without the coordination cost.

## Split when all three hold

1. **Two different workflows have run guided, end to end**:
   - UiO-66 AIMD, which is running now;
   - one ML workflow, e.g. labelling AIMD frames and then MACE fine-tuning.

   That puts load on hyogo beyond `cp2k_make_subsys`.
2. **hyogo owns its skills**: the CP2K single-point, geo-opt and AIMD
   recipes have moved from `codes/cp2k/` into `hyogo/`. rookery keeps build,
   benchmark and launch.
3. **No tool signature changed during the last workflow.** That is the sign
   the seams have settled.

Then split with `git filter-repo`, which keeps each folder's history. Give
each repo its own CI and plugin, keep the boundary test in each, and keep
densho (agent-notes) as the fourth.

## Working loop until then

1. Start a workflow in a fresh session in this folder. It needs the tools
   (registered locally) and the skills (the plugin, see below).
2. Follow the skill. When something is wrong or missing, the agent names the
   owning folder:
   - `yagura/` for structures and analysis;
   - `hyogo/` for methods and inputs;
   - `hpc_codes/`, `facilities/hokusai/` or `codes/` for HPC, builds and
     benchmarks.

   It fixes the problem there, with a test.
3. Re-render the skills, reinstall the plugin, continue.
4. At the end of the workflow: one commit per folder touched, then push to
   the fork.

## The missing piece: skills in a fresh session

The tools are registered (local `.venv` servers). The skills are not loaded:
they come with the plugin.

- Install only the **`hpc-hokusai` skill pack** from this checkout as a
  local marketplace. It registers no MCP servers, so it cannot clash with
  the local registrations.
- Don't install the base `hpc` plugin for now. Its `.mcp.json` fetches
  William's `main` and would duplicate the servers with older code.
- After a skill edit: re-render, bump the pack version, then update the
  marketplace and the plugin.
