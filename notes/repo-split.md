# Three repos: where today's pieces go

Proposal, 2026-11-04. Nothing has moved yet.

## The three domains

| repo | owns | does not own |
|---|---|---|
| **A. hpc-rookery** (this one) | facilities, schedulers, SSH, files; building, probing, dry-running, benchmarking a code on a machine; `software_registry` and `benchmarks` records | what a simulation *means*; structures |
| **B. simulation workflows** (name TBD) | how to run a method: MD, enhanced sampling, MLIP training and fine-tuning, active learning; input writing; method-level records (models, training runs) | where/how a binary runs on a given machine; structure maths |
| **C. structures and analysis** (name TBD) | building, converting and checking structures (ASE, pymatgen, spglib); trajectory and property analysis (RDF, MSD, VDOS, wavelet) | clusters, jobs, builds |

## Who leans on whom

```
B ──► A   which build exists, its layout, check_input, submit, fetch
B ──► C   build/check the structure before writing an input; analyse the output
C ──► A   only to run an analysis next to data that is too big to download
A ──► nothing
```

A stays ignorant of B and C. No cycles, so each can be released alone.
They talk through **MCP tools**, not Python imports: that avoids a second
`hpc_agent_core`-style shadowing problem and lets each repo pin its own
heavy dependencies (ASE/pymatgen stay out of A).

## Where today's pieces land

| piece today | goes to | note |
|---|---|---|
| facilities/, hpc_agent_core, hpc_mcp | A | unchanged |
| `cp2k/build` stage, `cp2k_check_build`, `software_registry` | A | builds are machine facts |
| `cp2k/benchmark` stage, `parse_bench.py`, `cp2k_bench_parse`, `benchmarks` records, dry-run tool | A | layouts are machine facts; the benchmark *input* comes from B |
| `cp2k_check_input` | A | it is "does this build accept this input", a probe |
| records adapter + schema (`hpc_codes/records.py`) | A, reused by B | same append-only YAML pattern; make the schema file per repo |
| `cp2k/single-point`, `geo-opt`, `aimd` stages (method knowledge) | B | keep a thin A-side note: the R×T and `srun` line come from A's records |
| `make_cp2k_md_input.py`, `make_subsys.py` → `cp2k_bench_input`, `cp2k_make_subsys` | B | `data_dir` comes from A's registry via `records_query` |
| your MACE fine-tuning / multinode debug skills | B | the Slurm-launch half leans on A |
| `structure-making` skill, `geomcheck.py`, `inspect_cif.py`, `to_extxyz.py` | C | `structure_check`, `cif_inspect`, `structure_to_extxyz` |
| shared `read_extxyz` / `valence` / `stretch_ch` | C (reader, stretch), B (valence: needs the CP2K basis file) | ends today's copy-paste |
| RDF, MSD, VDOS, wavelet | C (new) | |

## Conflicts with the current design, and the adaptation

1. **`structure_check` was planned inside `hpc_codes`, run on the login
   node.** Under the split it is C's. Adapt: write it as C's first tool,
   local by default. For a structure on the cluster, the agent fetches it
   with A's `fs_download` (structures are small), so C needs no SSH.
2. **"Every tool takes a facility" (AGENTS.md).** True for A only. C's
   tools take paths, not facilities; B's take a facility only where they
   call into A.
3. **Code packs are rendered per facility** (`{{CP2K_PARTITION}}` etc.).
   B's skills should be facility-neutral and *ask A* for those values
   (`records_query`, a future `code_values(facility, code)` tool), the
   direction the registry work already took when it replaced hard-coded
   paths. A keeps the per-facility overlays for build and benchmark.
4. **Records.** `software_registry` and `benchmarks` stay in A. B adds its
   own types (trained models, training/AL rounds) with the same adapter
   code; C probably needs none at first.
5. **Lab notebook** stays out of scope in all three.

## How to get there with the least risk

Stage it inside this repo first, the way rookery itself grew on a branch
of hpc-agent-core before it was split out:

1. Add two top-level packages here (e.g. `hpc_workflows/`, `hpc_structures/`)
   with their own MCP entry points, next to `hpc_codes`.
2. A conformance test enforces the arrows above: A's packages never import
   B or C; C never imports A.
3. Move pieces one commit at a time (table above), skills re-rendered.
4. When the boundaries hold, split each package into its own repo. That
   step is mostly `git filter-repo` and a new `.mcp.json`, and needs a
   decent connection (not on mobile data).

## Decisions (2026-11-04)

1. **A ↔ B contract: a launch tool in A.** B owns the input and the
   method; A owns the launch. One tool, roughly
   `launch_plan(facility, code, build_id=None, natoms, nodes)` →
   env script, R×T, the `srun` line, a cost estimate, all from
   `software_registry` and `benchmarks`. B never names a partition, a
   layout or an `srun` flag. Design this tool first: it is the seam.
2. **Analysis data: both routes are valid.** Download and analyse
   locally, or install only the needed analysis tools on the cluster and
   run them in a compute-node job (A builds/records that environment like
   any other software, then submits). C stays SSH-free either way.
3. **B is one repo for now**, with MD/sampling and MLIP as sub-packages;
   active learning joins them.

Still open: repo names; copy vs share the records adapter (lean: copy).

## Fourth piece: agent-notes (separate MCP server, exists)

Holds the formats and knowledge every repo's tools use across the
simulation lifecycle. Consequence: the records schema and adapter
(`hpc_codes/records.py`, `schemas/records.yaml`) are its natural home —
this was already the plan ("the notebook replaces the records skill").
That settles copy-vs-share: neither; A, B and C call agent-notes for
formats, and A keeps only the machine-fact record *types* it writes.

## Names (decided 2026-11-04)

- **A:** hpc-rookery (unchanged)
- **B workflows:** **hyogo** (兵庫, the classical armory: the store of
  methods and run recipes drawn on for each campaign)
- **C structures + analysis:** **yagura** (櫓, castle tower: built, then
  used as a lookout)
- **agent-notes → densho** (伝書, a scroll that hands a school's
  knowledge down: the formats and know-how every tool draws on)

Not yet checked for PyPI/GitHub clashes; distributions may need a prefix.

## Name candidates considered

Japanese military:

| repo | name | meaning | why |
|---|---|---|---|
| B workflows | **gunshi** (軍師) | the strategist who plans the campaign | picks the method and sequences the runs; leaves logistics to HQ |
| C structures + analysis | **nawabari** (縄張り) | laying out a castle's plan with ropes before building | designs structures; alt **yagura** (櫓), a tower that is built *and* used as a lookout, covering analysis too |
| agent-notes | keep, or **gunki** (軍記) | war chronicle | the record of every campaign |
| A (optional) | rookery, or **honjin** (本陣) | the general's headquarters camp | where every facility is commanded from |

Alternates: B `heiho` (兵法, art of war), `jinkei` (陣形, battle
formation); C `ishigaki` (石垣, fitted castle stone walls), `monomi`
(物見, scouting) for an analysis-only package.

Bird family, following "rookery" (a colony of nests):

| repo | name | why |
|---|---|---|
| B workflows | **murmuration** | a starling flock moving as one: many coordinated runs, ensembles, AL loops |
| C structures + analysis | **weaver** | weaver birds build intricate structures; also "weaves" analyses together |
| agent-notes | keep, or **fieldbook** | a naturalist's field notebook |

Alternates: B `flock`, `migration`; C `eyrie`, `plumage`, `nestcraft`.
Descriptive set if the theme gets in the way: `sim-workflows`,
`mol-forge` / `molkit`, `agent-notes`. Check PyPI and GitHub for clashes
before choosing (`weaver` and `flock` are likely taken as package
names; the repo can keep the name with an `hpc-`/`mol-` prefix on the
distribution).
