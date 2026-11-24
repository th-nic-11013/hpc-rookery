## HOKUSAI notes

- **Where to run:** CPU, partition `mpc`, 1 node, 1 process with 28 cores
  (`OMP_NUM_THREADS=28`). The `gpu` queue often waits many hours (see the
  mace-finetune notes); use it only for thousands of frames.
- **Measured CPU times** (28 threads, float64, 456-atom frames, job
  9900725): a float32-trained round-2 fine-tune run in float64 takes
  ~0.5 s/frame (24 frames 13 s, 7 frames 4–5 s); the MACE-MATPES-r2SCAN-0
  foundation ~8 s/frame (35 frames 278 s). Four fine-tunes on 66 frames
  plus the foundation on 35 took 4:49 min including the compare. Request
  1 h and 48 GB; the foundation dominates, so scale by its frame count.
- **Job spec:** nest the resources, or they are silently dropped. Flat keys
  such as `nodes`, `cpus_per_task` or `memory: "48G"` are ignored without a
  warning; without `memory`, Slurm on `mpc` gives 1 GB and float64
  MACE-MATPES on a few hundred atoms is OOM-killed (job 9895283):

  ```text
  "resources": {"node_count": 1, "process_count": 1,
                "cpu_cores_per_process": 28, "memory": 51539607552},   # 48 GiB, bytes int
  "attributes": {"queue_name": "mpc", "duration": "01:00:00"},
  "environment": {"OMP_NUM_THREADS": "28"}
  ```

  Check that `render_job_script` shows `--mem` and `--cpus-per-task`
  before submitting.
- **Environment:** `source ~/software/mlip/2026.10/mlip_env.sh`
  (mace-torch 0.3.14, ase, torch; the `mace` build's `env_script`).
- **Models:** the table in the mace-finetune notes. Both foundation models
  are float64 and D3-free: `--dispersion none`. The MP-0 head is `Default`,
  MatPES's `default`; leaving `--head` out takes each model's own.
  GPU-trained fine-tunes from `gpu` score fine on `mpc` with `predict.py`.
- **Login-node load tests:** `run_command_on_cluster` feeds no stdin, so
  `python - <<EOF` hangs until the ~120 s SSH limit and leaves a stray
  process. Use `python -c "..."`, and kill leftovers (`ps -u $USER`).
- **Compare on the cluster (low bandwidth):** hyogo is checked out at
  `~/agent/work/tools/hyogo-src`. Inside the job, after `predict.py`, run
  hyogo's compare functions directly (with `hyogo.mcp_server` stubbed in
  `sys.modules`, since the MCP package isn't installed there) and write a
  small `summary.json`; download only that. Reference scripts in the
  hpc-rookery repo: `notes/round2-eval/compare_on_cluster.py` (compare),
  `notes/round2-select/collect_on_cluster.py` (committee/selection).
- **No D3 on the cluster's Python side:** torch-dftd is not installed, so
  D3 forces can't be subtracted from labels that include them. Label
  D3-free frames instead.
