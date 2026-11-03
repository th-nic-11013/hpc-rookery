- **Wall time:** `mpc` allows 24 h. `mpc_l` allows 72 h, but only 8 nodes,
  and long jobs find backfill gaps less easily. Prefer 24 h segments on
  `mpc`.
- **Dependencies:** dependents of a failed `afterok` job are **never**
  cancelled automatically.
- **Files:** home has a 10-million-file quota.
