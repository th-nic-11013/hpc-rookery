# Where this repo comes from

Moved out of AGENTS.md on 2026-11-11 (kept verbatim).

## What this repo is (full)

One shared MCP-owning plugin (`plugins/hpc/`) plus one skill-only plugin per
facility (`plugins/hpc-<slug>/`), one MCP server process, every onboarded
facility reachable through one generic, facility-parametrized tool surface.

**This is `william-dawson/hpc-rookery`.** It began as the `unified-hub`
branch of `william-dawson/hpc-agent-core` and was split out into its own
repository; that branch remains there as the archive of this project's
development history, which this repo does not carry.

A rookery is a colony of nests, which is the architecture: many facilities,
one place. It is the unified form of what `hpc-agent-core` does per
machine. That project publishes a PyPI library which one-repo-per-machine
plugins (`Rikyu-Agent`, `Hokusai-Agent`, `RCCS-CloudAgent`, `Fugaku-Agent`,
...) each depend on; this repo absorbs the engine and every machine into a
single repo and a single server. Those per-machine repos are independent of
this one and continue to work.

Two consequences worth knowing before you change anything here:

- The `hpc_agent_core` package vendored here is **not** API-compatible with
  the `hpc-agent-core` published on PyPI — every entry point takes a
  `facility` argument. Don't port a change between them by copying;
  re-derive it.
- Because this repo ships a package *directory* named `hpc_agent_core`,
  installing it shadows the PyPI `hpc-agent-core` when both land in one
  environment. The repo name no longer hints at that, so it is easy to trip
  over. The plugin's `.mcp.json` uses `uv tool run --from git+...@main`,
  which is isolated, so this only bites if you hand-install both into the
  same venv.

