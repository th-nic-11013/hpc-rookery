# Review backlog

Moved out of AGENTS.md on 2026-11-11 so it is not loaded into every agent session. Read it when working on one of these items.


Review carried over from the `unified-hub` branch this repo was split
from (2026-09-01); the commit range it covered is not in this history. Each item says whether it was
reproduced or is still a reading of the code — don't downgrade an
unverified one without checking it, and don't re-verify one marked
**verified** from scratch.

Two of these are only visible with an *empty* `~/.hpc-agent/`. A developer
machine has config for its own clusters, so both the CI failure and the
unconfigured-tool behaviour pass locally and fail in CI. Reproduce with:

```bash
.venv/bin/python tests/notebook_client.py --empty-home
.venv/bin/python tests/conformance.py --empty-home
```

### P1

- ~~**The `fs_rm` refusal test can delete the test runner's own files.**~~
  **Fixed.** `fs_rm_refuses_dangerous_paths` now patches
  `hpc_mcp.hpc_server.run_command` with a stub that raises, and points
  `CELL2026_HOST` at the unresolvable `fs-rm-test.invalid`, so a guard
  regression fails the test without reaching any shell. Checked by dropping
  `"."` from `_RM_REFUSED` (`"~"` alone is redundant: `norm_path` maps it
  to `"."`): the test failed naming `rm -- .`, and a canary in a temporary
  `HOME` survived.

- ~~**The configuration gate also blocks the tools that need no cluster.**~~
  **Fixed.** `get_facility`, `render_job_script` and the three docs tools
  no longer carry `@_configured_facility_tool`: the first two are packaged
  facts and pure local rendering, and the docs tools read a packaged index,
  so none can "present an unconfigured facility as ready". The never-fail
  invariant check is restored, now asserted through the MCP entry points
  rather than the helpers underneath — the earlier version passed while
  `get_facility` itself was raising. `every_facility_scoped_mcp_tool_checks_
  configuration_first` now derives the ungated set from the source and
  compares it against an explicit three-name allowlist, so both halves of
  the contract are enforced: a cluster-bound tool added without the guard
  fails, and so does re-gating a packaged-data one.

### P2

- **Miyabi loses `PBS_O_WORKDIR` to line wrapping.** `_parse_blocks`
  (`facilities/miyabi/compute.py`) keeps only lines containing `" = "`, so
  PBS's tab-indented continuation lines are silently dropped. `qstat -f`
  wraps attribute values at ~80 columns, and `PBS_O_WORKDIR` sorts *after*
  `PBS_O_PATH` inside `Variable_List`, so it falls past the first wrap and
  `_pbs_workdir` returns `""`. Use `qstat -f -w`, or append continuation
  lines to the previous key in `_parse_blocks`.
  **Verified offline** (parsed value truncates mid-`PBS_O_LOGNAME`);
  **not verified live** — the multiplexed master had expired, and
  re-checking needs the user's one-time code.

- **`live_smoke.py`'s new Miyabi workdir assertion may fail the `--job`
  tier.** `assert workdir.startswith("/")` runs after the job reaches a
  terminal state, where `get_statuses` can fall through to `_parse_history`,
  whose `meta_data` has no `workdir` key at all. With the wrapping bug above
  this asserts on `""`, and the miyabi `candidates` list collapses to one
  unusable path. **Not verified** — blocked on the same live access.

- **The universal staging bullet contradicts Fugaku's own quota warning.**
  In `plugins/hpc-fugaku/skills/fugaku-submitting-jobs/SKILL.md`, line 64
  says jobs run from the group data area and *not* `$HOME`, because home is
  a 20 GiB area whose quota large outputs "can exhaust mid-run"; line 107 —
  from `templates/skills/submitting-jobs.md.tmpl` — says to stage run
  directories under `~/agent/work/`, which is in home. The universal bullet
  is the more prescriptive of the two, so an agent follows it into exactly
  the failure the facility note warns about. Give the template bullet an
  "unless the facility notes say otherwise" carve-out, or let
  `{{FACILITY_NOTES}}` override it. **Verified** in the rendered skill.

- **cell2026's demo aborts on a correctly configured machine.** The demo
  skill calls `get_projects` "to prove SSH is actually reachable" and says
  to stop if it errors, but cell2026's `get_projects` now unconditionally
  raises `NotImplementedError` (changed from `return []`).
  Fugaku's `skill_notes/demo.md` was updated for this same mismatch;
  cell2026's was not. **Verified** — the call raises; note the skill's stop
  condition says "isn't configured" while the error says "does not
  support", so the abort is likely but not certain.

- **CI's fork-PR message sends contributors into a loop.** The step runs
  three generators (`.github/workflows/ci.yml:48-50`) but the error at
  line 65 names only `render_facility_tables.py` and `render_skills.py`. A
  fork contributor follows it verbatim, never runs `render_plugins.py`, and
  fails again on the same step with the same message. **Verified** by
  reading the workflow.

### P3

- **`rccs_cloud`'s embeddings are stale relative to its chunks.** The
  MPICH guide edit regenerated `chunks.json` (last commit 2026-08-30) but
  not `embeddings.npy` (2026-08-18). The shapes still match, so nothing
  raises; semantic search just ranks on the old OpenMPI text. Re-run the
  ingest and commit the `.npy`. **Verified** from git dates.
- **`rccs_cloud` prose still says OpenMPI.** `skill_notes/submitting-jobs.md`
  now names `mpi/mpich-x86_64` but the sentence still reads "launch with
  `mpirun` (OpenMPI) instead", and the following paragraph's hostfile/PMI
  reasoning is OpenMPI-specific. Re-check against MPICH's Hydra.
- **Orphaned generated-skill directories are no longer detected.**
  The check was removed wholesale so hand-authored custom skills
  would not be flagged. Generated and hand-written skills are
  indistinguishable on disk today, which is why it had to go rather than be
  refined; treating only `<slug>-<workflow>` directories as managed (or
  adding a frontmatter marker) would restore it. `render_plugins.py` also
  never removes a stale `plugins/hpc-<slug>/`.
- **`render_plugins.py`'s `manifest_version` never bumps, and reads only
  the Codex manifest.** Regenerating after a description change rewrites
  the manifest without a version bump, so clients cache stale skills; and a
  version bumped in `.claude-plugin/plugin.json` is overwritten with
  Codex's value on the next render. The bump half is addressed (2026-11-11):
  gap branches no longer bump, and `render_plugins.py --bump [REF]`, run once
  per merge, minor-bumps every plugin whose files changed since REF. The
  Codex manifest is still the only version source.
- **`_parse_group_point` calls `int()` unguarded.** Four CSV cells are
  converted without a `try`, and `_group_point_or_none` catches only
  `RuntimeError`, so one odd row would take down `get_projects` for every
  group. **Checked live:** Fugaku emits clean integers for all of
  `ra000009`, `ra250029`, `hp250291`, and `trial` correctly has no
  `GROUP_POINT` row at all — so this is robustness, not a live defect.

### Housekeeping

- **`live_smoke.py` still aborts the whole run on an unreachable facility.**
  The agreed change — skip it and report the reason, since no one person
  has access to every machine — was never implemented; a later commit touched
  that file for the Miyabi workdir assertion instead.

After fixing these, rerun `tests/conformance.py`, `tests/notebook_client.py`
(also with `--empty-home`), all three generated-output checks,
`git diff --check`, and the wheel-data check.

