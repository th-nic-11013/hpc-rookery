# How to use hpc-rookery, hyogo and yagura

2026-11-04. A short guide for one person working on HOKUSAI.

## The idea in one picture

```
you (plain English) → AI agent (Claude Code / Codex)
                         │ reads the skills (step-by-step recipes)
                         │ calls the MCP tools
        ┌────────────────┼─────────────────┐
     yagura            hyogo             rookery
  structures &      method inputs     HPC: upload, launch_plan,
  analysis          (cp2k_make_subsys) submit, monitor, download,
  (check, fix,                         build checks, benchmarks,
   RDF/MSD/VDOS)                       records
```

You don't call the tools yourself. You tell the agent what you want, and the
skills tell it which tools to call and in what order. Each of the three only
talks to the others through the agent, so they can later become separate
repos without changing anything for you.

| piece | ask it for | its tools |
|---|---|---|
| **yagura** (櫓) | "is this structure OK?", "convert this CIF", "RDF / MSD / VDOS of the trajectory" | `structure_check`, `structure_stretch_xh`, `cif_inspect`, `structure_to_extxyz`, `trajectory_rdf`, `trajectory_msd`, `trajectory_vdos` |
| **hyogo** (兵庫) | "write the CP2K input for this structure" | `cp2k_make_subsys` (more MD/MLIP workflow tools later) |
| **rookery** | "run it on HOKUSAI", "how many nodes?", "what does it cost?", "is the job done?" | `launch_plan`, `fs_upload`, `submit_job`, `get_statuses`, `fs_download`, `cp2k_check_input`, `cp2k_kinds`, records tools, … |

## One-time setup

1. **SSH:** `ssh hokusai` works from your Mac. It already does.
2. **Facility config:** `~/.hpc-agent/hokusai.json`. You already have it.
   Check it with `hpc-doctor hokusai`.
3. **Register the five servers with your agent.**
   - **Today, from your local checkout.** The published plugin still points at
     William's `main`, which doesn't have hpc-codes, yagura or hyogo yet.
     First reinstall the checkout so the `yagura-mcp` and `hyogo-mcp`
     commands exist:

     ```bash
     cd ~/Downloads/research/hpc-rookery && uv pip install -e '.[yagura]'
     ```

     Then add each server to Claude Code:

     ```bash
     for s in hpc hpc-docs hpc-codes yagura hyogo; do claude mcp add $s -- ~/Downloads/research/hpc-rookery/.venv/bin/$s-mcp; done
     ```

     (`hpc` → `hpc-mcp`, `hpc-docs` → `hpc-docs-mcp`, and so on.)
   - **Later, as a plugin:** once the plugin's `.mcp.json` points at your
     fork (or William merges), install it with
     `/plugin install hpc` then `hpc-hokusai` from the marketplace. The
     plugin also brings the skills.
4. Restart the agent. Ask "what HPC facilities do you have?". It should
   list HOKUSAI.

## A typical session (what we're doing with UiO-66 now)

| you say | the agent does |
|---|---|
| "Here's `uio66_prim_114.xyz`. Is it ready for DFT?" | **yagura** `structure_check` flags the short C–H bonds; it offers `structure_stretch_xh` |
| "Fix it and set up a 300 K AIMD with CP2K on HOKUSAI" | **rookery** `cp2k_kinds` (pseudopotentials from the build), **hyogo** `cp2k_make_subsys`, then it fills in the AIMD input template |
| "How much will 1000 steps cost?" | **rookery** `launch_plan` finds the matching benchmark: 1 node, 16×7, about 160 core-hours. It **asks you** before submitting |
| "Go" | `fs_upload`, `cp2k_check_input`, `submit_job` |
| "Is it done?" | `get_statuses` |
| "Analyse it" | `fs_download` the trajectory, then **yagura** `trajectory_rdf`, `trajectory_msd` and `trajectory_vdos` |

The agent always asks before anything that costs core-hours or deletes files.

## Without an agent (by hand)

Every tool is a plain Python function, so you can script it:

```bash
cd ~/Downloads/research/hpc-rookery
.venv/bin/python -c "from yagura import server as y; print(y.structure_check('my.xyz'))"
```

Some yagura tools are also standalone scripts (`yagura/geomcheck.py`,
`to_extxyz.py`, `inspect_cif.py`).

## Where things live

- Skills (the recipes): `codes/cp2k/<stage>/SKILL.md` and
  `codes/common/structure-making/`. They are rendered into
  `plugins/hpc-hokusai/`.
- HOKUSAI-specific values: `facilities/hokusai/codes/cp2k/values.json`.
- Measured benchmarks: `facilities/hokusai/records/` (shared) and
  `~/.hpc-agent/records/hokusai/` (yours).
- Run directories on HOKUSAI: `~/agent/work/…`.
