#!/bin/bash
# CP2K ranks x threads benchmark on one exclusive node, as one job.
# Copy this file into the benchmark folder, edit the BENCHMARK SETTINGS block, then
# submit FROM THAT FOLDER (the folder becomes BENCH):  sbatch bench_job.sh
# Stages run in order; each starts only if the one before passed:
#   stage1_smoke.sh     SMOKE_INPUT in SMOKE_LAYOUTS
#   stage2_scan.sh      SCAN_INPUT in SCAN_LAYOUTS
#   stage3_validate.sh  VALIDATE_INPUT with the best stage-2 layouts (or skipped)
# A stage passes only if all its runs exit 0, reach PROGRAM ENDED and agree in
# energy; it stops at its first failed run, and the job then exits 1.
# Results: runs/<jobid>/stage{1,2,3}/; summary at the end of the log and in results.csv.
# Cost: 112 cores x --time at most (no run is started that could
# go past the limit, so OverTimeLimit is never used); expected: EDIT ME.
#------ slurm option --------#
#SBATCH --partition=mpc
#SBATCH --account=RBxxxxxx
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=112
#SBATCH --cpus-per-task=1
#SBATCH --exclusive
#SBATCH --time=0:53:00
#SBATCH --job-name=cp2k-bench
#SBATCH --output=%x_%j.log

#------- BENCHMARK SETTINGS (edit) -------#
export BENCH_LIMIT_S=$(( 53 * 60 ))     # keep equal to --time above
CP2K_ENV=${CP2K_ENV:?set CP2K_ENV to the env_script of the build (software_registry)}
export SMOKE_INPUT=H2O-32.inp     SMOKE_TIMEOUT=120
export SCAN_INPUT=H2O-64.inp      SCAN_TIMEOUT=240
export VALIDATE_INPUT=H2O-128.inp VALIDATE_TIMEOUT=360    # or none
# export SMOKE_LAYOUTS="28x4 8x14"
# export SCAN_LAYOUTS="112x1 56x2 28x4 16x7 8x14 4x28"
# export VALIDATE_PICK=3

#------- Program execution -------#
export BENCH_START=$(date +%s)
export BENCH=${BENCH:-$(pwd)}
[ -f "${BENCH}/run_layouts.sh" ] || { echo "submit from the benchmark folder (no run_layouts.sh in ${BENCH})"; exit 1; }
for f in ${SMOKE_INPUT} ${SCAN_INPUT} ${VALIDATE_INPUT}; do
    [ "${f}" = "none" ] || [ -f "${BENCH}/inputs/${f}" ] || { echo "missing inputs/${f}"; exit 1; }
done

source "${CP2K_ENV}"
command -v cp2k.psmp > /dev/null || { echo "cp2k.psmp not on PATH after ${CP2K_ENV}"; exit 1; }
echo "=== $(date) job ${SLURM_JOB_ID} on $(hostname)"; lscpu | grep -E "^NUMA node[0-9]"

summary() {
    echo "=== $(date) summary"
    /usr/bin/python3.12 -I ${BENCH}/parse_bench.py ${BENCH}/runs/${SLURM_JOB_ID}/stage*
}

for STAGE in stage1_smoke stage2_scan stage3_validate; do
    echo; echo "##### ${STAGE} #####"
    if ! bash ${BENCH}/${STAGE}.sh; then
        echo "##### ${STAGE} failed: later stages skipped #####"
        summary
        exit 1
    fi
done
summary
echo "=== $(date) all stages passed"
