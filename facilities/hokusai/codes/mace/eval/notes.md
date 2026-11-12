## HOKUSAI notes

- **Where to run:** a few to a few hundred frames of ~500 atoms run on
  CPU in minutes: partition `mpc`, 1 node, 1 process with 28 cores
  (`OMP_NUM_THREADS=28`), 30 min. T1 (job 9751103: two float64 models,
  six 456-atom frames) took about 2 minutes. The `gpu` queue often waits
  many hours (see the mace-finetune notes); use it only for thousands of
  frames.
- **Environment:** `source ~/software/mlip/2026.10/mlip_env.sh`
  (mace-torch 0.3.14, ase, torch; the `mace` build's `env_script`).
- **Models:** the table in the mace-finetune notes. Both foundation models
  are float64 and D3-free: `--dispersion none`. The MP-0 head is `Default`,
  MatPES's `default`; leaving `--head` out takes each model's own.
- **No D3 on the cluster's Python side:** torch-dftd is not installed, so
  D3 forces can't be subtracted from labels that include them. Label
  D3-free frames instead.
