# Why this repo must never publish `hpc_agent_core` to PyPI

Moved out of AGENTS.md on 2026-11-11 (kept verbatim); the rule itself stays there.

## This repo must never publish `hpc_agent_core` to PyPI

`william-dawson/hpc-agent-core` publishes the real `hpc-agent-core` library
on a released tag. **This repo must not.** The `hpc_agent_core` package
here is a divergent fork of that engine, and publishing it would either
create a confusing second package or — far worse — ship an
API-incompatible `hpc_agent_core` to everyone depending on the real one.

Splitting out of that repository removed two of the barriers that used to
make this impossible, so the remaining ones matter more, not less:

- there is **no publish workflow here**. Don't add one. (`hpc-agent-core`
  has its own, triggered by `release: published`; it lives in that
  repository and nothing here can fire it.)
- `.github/workflows/ci.yml` grants only `contents: write`. PyPI trusted
  publishing requires `id-token: write`, so no step in this workflow can
  authenticate to PyPI even if one were added.
- the distribution is named `hpc-rookery`, so it could not overwrite
  `hpc-agent-core` on the index in any case.

If this project is ever published deliberately, it goes out as
`hpc-rookery` and never as `hpc-agent-core`.

