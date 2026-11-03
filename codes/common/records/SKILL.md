---
name: {{SLUG}}-records
description: Read and write measured records for {{DISPLAY_NAME}}: benchmark timings (best MPI x OpenMP layout, s per step, node scaling), the user's software builds, and the job IDs behind them. Use whenever another skill says to query or append `benchmarks`, `software_registry` or `benchmarks_provenance` records, or the user asks what has already been measured or built on {{DISPLAY_NAME}} ("is there a benchmark for this", "which layout is fastest", "what builds do I have").
---

# Records on {{DISPLAY_NAME}}

Skills keep measured facts in records, never in their own prose. They name
record types and fields; **only this skill knows where the records are
kept**. A lab-notebook tool will later replace this file backing; skills that
go through this skill will not change.

## Contract

- **Reads:** every record type, both scopes. Facility records ship with
  this skill; the user's records are in the user's home on this computer.
- **Writes:** append only. A record is never edited or removed; a correction
  is a new record whose `supersedes` names the old id.
- **Tools:** no MCP tools. `scripts/records.py` runs **locally** (this
  computer, not the cluster).
- **Preconditions:** Python with PyYAML. If `python3 -c "import yaml"` fails,
  run the script with `uv run --quiet --no-project --with pyyaml python -I`.
- **Done when:** a query printed its matches, or an append printed
  `appended ...`. A refused append prints every reason; fix the record and
  try again.

## Record types

The full field list is in `schema.yaml` beside this file (provisional,
`schema_version: 1`).

| type | scope | id | holds |
|---|---|---|---|
| `benchmarks` | facility | `id` | one timing: system × method × run type × layout × nodes |
| `software_registry` | user | `build_id` | one installed build: path, env script, test status, `default` |
| `benchmarks_provenance` | user | `id` | the job IDs behind a `benchmarks` record |

## Query

```bash
python3 -I <this skill>/scripts/records.py query benchmarks \
  code=cp2k run_type=aimd_step system.label=UiO-66-conv valid=true \
  --fields=nodes,ranks_per_node,threads_per_rank,metric.value,date
```

- A filter is a field path, `=`, and a value. Values match exactly, so
  `nodes=2` and `version=2026.2` both work.
- `--fields` keeps the output short. Without it, whole records print.
- Each hit carries `_scope: facility` (shared) or `_scope: user` (measured by
  this user and not yet shared).
- There is no range or nearest match yet. To find a *similar* system,
  query by `code`, `run_type` and `partition`, then compare `system.atoms`,
  `system.elements` and `method.*` yourself.
- `check` validates every file and the links between records. Run it after
  appending several records.

## Append

Write the record as YAML following `schema.yaml`, then:

```bash
python3 -I <this skill>/scripts/records.py append benchmarks record.yaml   # or - for stdin
```

- The record goes to the **user** scope even for `benchmarks`: the installed
  skill is a copy, so writing beside it would be lost on update.
- Keep the scheduler job ID in the user's `benchmarks` record (`job_id`) or
  in `benchmarks_provenance`.
- Use stable ids. For a benchmark:
  `<facility>-<code>-<date>-<system>-<run type>-<nodes>n-<R>x<T>`, e.g.
  `{{SLUG}}-cp2k-2026-10-22-uio66conv-aimdstep-2n-16x7`.
- A benchmark that failed or did not converge is still recorded, with
  `valid: false` and a `reason`, so it is not repeated.

## Sharing a benchmark

User-scope benchmarks help only this user. Shared records live in the
hpc-rookery repository (`facilities/{{SLUG}}/records/benchmarks.yaml`). To share one:
1. copy it there with `job_id: null`;
2. keep the job ID in the user's `benchmarks_provenance`;
3. run `check`;
4. open a pull request.

Ask the user before doing any of this. It publishes their results.
