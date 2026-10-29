HBW2 is CPU-first, so demo the default `mpc` partition (312 nodes, the
least contended) rather than the small GPU subsystem:

```json
{
  "name": "hokusai-demo",
  "executable": "hostname && echo '---' && lscpu | head -20",
  "resources": {"node_count": 1, "processes_per_node": 1},
  "attributes": {"duration": 300, "queue_name": "mpc"}
}
```

`queue_name` and `account` are both filled in automatically from the
configured defaults if omitted — but Step 1's `get_projects` output is worth
showing either way, since **fair-share standing is what determines queue
order here**. This job bills 1 core for at most 5 minutes.

Don't be alarmed if `get_resources` shows `mpc` at 0 idle nodes: nodes are
shared core by core and HBW2 backfills aggressively, so with good
fair-share standing a short job often starts within seconds (verified — a
1-node job and even a 64-node job each started immediately against a
fully-allocated partition). With low standing the same 1-core job has
waited about 30 minutes, reason `Priority`. Report what the job actually
does rather than predicting either outcome from the idle count, and tell
the user it may take a while if their project's fair-share factor is low.

Don't demo on `gpu`/`gpu_i`: the GPU nodes are usually fully allocated, and
a 1-GPU, 5-minute job has waited 17.5 hours.
