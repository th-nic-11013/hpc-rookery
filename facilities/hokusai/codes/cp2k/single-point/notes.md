- **Node:** {{CP2K_CORES_PER_NODE}} cores in 8 NUMA domains of 14, ~16 GB of
  HBM per domain, no DDR. The layout and binding details are in
  `{{SLUG}}-cp2k-benchmark`'s `references/facility.md`.
- **Memory:** leave `resources.memory` unset. All 112 cores bring 112 GiB,
  and memory above 1 GiB/core is billed as extra cores.
- **MPI:** the builds use the site Open MPI, which needs `--mpi=pmix`.
- **Limits:** 64 nodes and 24 h on `mpc` (`mpc_l`: 8 nodes, 72 h). A job
  over a limit is accepted but never starts.
