---
name: {{SLUG}}-records
description: Read and write measured records for {{DISPLAY_NAME}}: benchmark timings (best MPI x OpenMP layout, s per step, node scaling), the user's software builds, and the job IDs behind them. Use whenever another skill says to query or append `benchmarks`, `software_registry` or `benchmarks_provenance` records, or the user asks what has already been measured or built on {{DISPLAY_NAME}} ("is there a benchmark for this", "which layout is fastest", "what builds do I have").
---

# Records on {{DISPLAY_NAME}}

Skills keep measured facts in records, never in their own prose. They name
record types and fields, and read and write them through the three
`records_*` tools of the `hpc` server. **Only those tools know where
the records are kept.** A lab-notebook backend will later replace them
behind the same tool names; skills will not change.

## Contract

- **Reads:** every record type, both scopes: the facility's shared records
  (shipped with the server) and the user's own (on this computer).
- **Writes:** append only. A record is never edited or removed; a correction
  is a new record whose `supersedes` names the old id.
- **Tools:** `records_query`, `records_append`, `records_check`,
  `cp2k_record_from_run` (server `hpc`), each with `facility="{{SLUG}}"`. They work on local files
  and never contact the cluster.
- **Preconditions:** the `hpc` server is connected. If the tools are
  missing, say so; don't read or write the record files by hand.
- **Done when:** a query returned its matches, or an append returned
  `{"id", "scope": "user", ...}`. A refused append lists every reason; fix
  the record and try again.

## Record types

The full field list is in `schema.yaml` beside this file (provisional,
`schema_version: 1`).

| type | scope | id | holds |
|---|---|---|---|
| `benchmarks` | facility | `id` | one timing: system × method × run type × layout × nodes |
| `software_registry` | user | `build_id` | one installed build: path, env script, data dir, test status, `default`, `removed` |
| `benchmarks_provenance` | user | `id` | the job IDs behind a `benchmarks` record |

## Query

```text
records_query(facility="{{SLUG}}", record_type="benchmarks",
              filters={"code": "cp2k", "run_type": "aimd_step",
                       "system.label": "UiO-66-conv", "valid": true},
              fields=["nodes", "ranks_per_node", "threads_per_rank", "metric.value", "date"])
```

- `filters` maps a field path to a value. Values match exactly; a number or
  a version matches whether it is stored as text or as a number.
- `fields` keeps the result short. Without it, whole records return.
- Each hit carries `_scope: facility` (shared) or `_scope: user` (measured by
  this user and not yet shared).
- There is no range or nearest match yet. To find a *similar* system,
  query by `code`, `run_type` and `partition`, then compare `system.atoms`,
  `system.elements` and `method.*` yourself.
- Builds: the one to use when the user names none is
  `filters={"code": "cp2k", "default": true}`. Never use a build whose
  `removed` is set.
- `records_check(facility="{{SLUG}}")` validates every record and the links
  between them. Run it after appending several records.

## Append

Build the record as a mapping following `schema.yaml`, then:

```text
records_append(facility="{{SLUG}}", record_type="benchmarks", record={...})
```

- The record goes to the **user** scope even for `benchmarks`: the shared
  records are part of the installed package, so writing there would be lost
  on update.
- Keep the scheduler job ID in the user's `benchmarks` record (`job_id`) or
  in `benchmarks_provenance`.
- CP2K runs need no hand-written records: `cp2k_bench_records` builds them
  for benchmark jobs (see the cp2k-benchmark skill), and
  `cp2k_record_from_run` for a production run (single point, optimisation,
  MD segment) from a `cp2k_parse_output` result and `like` = a record of
  the same system and method.
- Use stable ids. For a benchmark:
  `<facility>-<code>-<date>-<system>-<run type>-<nodes>n-<R>x<T>`, e.g.
  `{{SLUG}}-cp2k-2026-10-22-uio66conv-aimdstep-2n-16x7`.
- A benchmark of an ML potential (MACE in LAMMPS or ASE) records
  `method.model`, `precision` (f32/f64), `interface` and `device` instead
  of the DFT fields (functional, basis, cutoff_ry, hybrid, scf, which it
  must not carry), with `run_type: md_step` and `system.kpoints: none`.
  `extra` can hold `ns_per_day`, `nve_drift_meV_atom_ps` and
  `concurrent_runs` (copies run side by side; the metric is per copy).
- A benchmark that failed or did not converge is still recorded, with
  `valid: false` and a `reason`, so it is not repeated.
- Changing an existing record (moving `default` to another build, setting
  `removed`) is not an append. Tell the user; the file is theirs to edit
  (`~/.hpc-agent/records/{{SLUG}}/<type>.yaml`).

## Sharing a benchmark

User-scope benchmarks help only this user. Shared records live in the
hpc-rookery repository (`facilities/{{SLUG}}/records/benchmarks.yaml`). To share one:
1. copy it there with `job_id: null`;
2. keep the job ID in the user's `benchmarks_provenance`;
3. run `records_check`;
4. open a pull request.

Ask the user before doing any of this. It publishes their results.
