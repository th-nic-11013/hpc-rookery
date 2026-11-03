#!/bin/bash
# CP2K ranks x threads test on N exclusive nodes: one MD input, several
# per-node layouts, as one job. Use it only after the single-node scan passed and a
# 1-node reference run of the same input exists (SKILL.md step 5c).
# Copy into the benchmark folder, edit the BENCHMARK SETTINGS block, then submit FROM
# THAT FOLDER (the folder becomes BENCH):  sbatch multinode_layouts_job.sh
# LAYOUTS are per node and run in the order given. The first is the baseline (normally
# the single-node winner): if it fails, the job stops. A later layout that fails
# (timeout, out of memory, SCF) is recorded and the job goes on with the next one.
# STOP_ON_FIRST=0 treats the first layout like the others (no baseline in this job).
# CMP_DIR/CMP_LAYOUT optionally name an earlier N-node run (total RxT) to compare with.
# Every run gets the same timeout, from the 1-node reference:
#   predicted = SCALE x (first step + (STEPS - 1) x steady step) + LAUNCH_S
#   timeout   = predicted + 60 s
# and run_layout never starts a run that could outlast --time (timeout + 90 s needed).
# So --time must be at least about (number of layouts) x (timeout + 90 s) + 60 s.
# Results: runs/<jobid>/stage_multinode/; summary table, energy/SCF check and the
# speed-up and parallel efficiency of each layout against the reference at the end
# of the log.
# Cost: NODES x {{CP2K_CORES_PER_NODE}} cores x --time at most (EDIT ME with the real
# numbers); expected: EDIT ME.
#------ slurm option --------#
#SBATCH --partition={{CP2K_PARTITION}}
#SBATCH --account=RBxxxxxx
#SBATCH --nodes=4
#SBATCH --ntasks-per-node={{CP2K_CORES_PER_NODE}}
#SBATCH --cpus-per-task=1
#SBATCH --exclusive
#SBATCH --time=0:20:00
#SBATCH --job-name=cp2k-mn-layouts
#SBATCH --output=%x_%j.log

#------- BENCHMARK SETTINGS (edit; the ${X:-...} form lets dryrun.sh override) -------#
export BENCH_LIMIT_S=${BENCH_LIMIT_S:-$(( 20 * 60 ))}   # keep equal to --time above
export BENCH_NODES=4                    # keep equal to --nodes above
CP2K_ENV=${CP2K_ENV:-{{CP2K_DEFAULT_ENV}}}
INPUT=${INPUT:-NAME.inp}                # MD input in inputs/
STEPS=${STEPS:-10}                      # MD steps in INPUT
LAYOUTS=${LAYOUTS:?per-node layouts, baseline first}  # R x T = {{CP2K_CORES_PER_NODE}}; choose from the records
REF_DIR=${REF_DIR:-runs/JOBID/scaling}  # 1-node run of INPUT (relative to BENCH, or absolute)
REF_LAYOUT=${REF_LAYOUT:?layout of the 1-node reference run}
SCALE=${SCALE:-0.5}                     # expected N-node : 1-node time (0.5 = at least 2x faster)
LAUNCH_S=${LAUNCH_S:-120}               # srun launch + CP2K start-up allowance (up to 85 s seen)
STOP_ON_FIRST=${STOP_ON_FIRST:-1}       # 1: first layout is the baseline, its failure stops the job
CMP_DIR=${CMP_DIR:-}                    # optional earlier N-node run to compare with (or empty)
CMP_LAYOUT=${CMP_LAYOUT:-}              # its total layout, e.g. 64x7

#------- Program execution -------#
export BENCH_START=$(date +%s)
export BENCH=${BENCH:-$(pwd)}
export STAGE=stage_multinode
[ -f "${BENCH}/run_layouts.sh" ] || { echo "submit from the benchmark folder (no run_layouts.sh in ${BENCH})"; exit 1; }
[ -f "${BENCH}/inputs/${INPUT}" ] || { echo "missing inputs/${INPUT}"; exit 1; }
[ "${SLURM_JOB_NUM_NODES}" = "${BENCH_NODES}" ] || { echo "job has ${SLURM_JOB_NUM_NODES} nodes, BENCH_NODES=${BENCH_NODES}"; exit 1; }
[[ ${REF_DIR} = /* ]] || REF_DIR=${BENCH}/${REF_DIR}
[ -z "${CMP_DIR}" ] || [[ ${CMP_DIR} = /* ]] || CMP_DIR=${BENCH}/${CMP_DIR}
source "${BENCH}/run_layouts.sh"

read -r STEP1 FIRST1 N1 < <(${PY} --stats ${REF_LAYOUT} "${REF_DIR}") \
    || stage_fail "no finished ${REF_LAYOUT} run in ${REF_DIR}"
if [ -n "${CMP_DIR}" ]; then
    read -r STEPC FIRSTC NC < <(${PY} --stats ${CMP_LAYOUT} "${CMP_DIR}") \
        || stage_fail "no finished ${CMP_LAYOUT} run in ${CMP_DIR}"
fi
source "${CP2K_ENV}"
command -v cp2k.psmp > /dev/null || stage_fail "cp2k.psmp not on PATH after ${CP2K_ENV}"
echo "=== $(date) job ${SLURM_JOB_ID} on ${SLURM_JOB_NODELIST} (${BENCH_NODES} nodes)"

PRED=$(awk -v s="${STEP1}" -v f="${FIRST1}" -v n="${STEPS}" -v k="${SCALE}" -v l="${LAUNCH_S}" \
           'BEGIN { printf "%d", k * (f + (n - 1) * s) + l }')
TMO=$(( PRED + 60 ))
NL=$(wc -w <<< "${LAYOUTS}")
NEED=$(( NL * (TMO + 90) + 60 ))
echo "=== reference ${REF_DIR} ${REF_LAYOUT}: ${STEP1} s/step, first step ${FIRST1} s"
echo "=== ${NL} layouts per node: ${LAYOUTS}; predicted ${PRED} s per run, timeout ${TMO} s; all runs need ${NEED} s of ${BENCH_LIMIT_S} s"
[ ${NEED} -le ${BENCH_LIMIT_S} ] || echo "=== WARNING: not every layout fits; the last ones will not start (exit 125)"

n=0; failed=""
for L in ${LAYOUTS}; do
    R1=${L%x*}; T=${L#*x}; n=$(( n + 1 ))
    [ $(( R1 * T )) -eq ${NODE_CORES:-{{CP2K_CORES_PER_NODE}}} ] || stage_fail "layout ${L} does not fill a node"
    run_layout ${INPUT} $(( R1 * BENCH_NODES )) ${T} 1 ${TMO}
    rc=$?
    [ ${rc} -eq 0 ] && continue
    [ ${n} -eq 1 ] && [ "${STOP_ON_FIRST}" = 1 ] && stage_fail "baseline ${L} failed (exit ${rc}); other layouts skipped"
    failed="${failed} ${L}(exit ${rc})"
    [ ${rc} -eq 125 ] && break          # out of job time: later runs would not fit either
done

echo "=== $(date) summary"
${PY} "${REF_DIR}" ${CMP_DIR:+"${CMP_DIR}"} "${STAGEDIR}"
[ -z "${CMP_DIR}" ] || echo "=== comparison run ${CMP_DIR} ${CMP_LAYOUT}: ${STEPC} s/step, first step ${FIRSTC} s"
${PY} --check "${STAGEDIR}"; ok=$?
for L in ${LAYOUTS}; do
    R1=${L%x*}; T=${L#*x}; R=$(( R1 * BENCH_NODES ))
    read -r STEPN FIRSTN NN < <(${PY} --stats ${R}x${T} "${STAGEDIR}") || continue
    awk -v l="${L}" -v r="${R}" -v t="${T}" -v n="${BENCH_NODES}" -v a="${STEP1}" -v b="${STEPN}" \
        -v fa="${FIRST1}" -v fb="${FIRSTN}" 'BEGIN {
        printf "=== %s per node (%dx%d): %.2f s/step -> %.2fx the 1-node reference, efficiency %.0f %%; first step %.1f s (%.2fx)\n",
               l, r, t, b, a/b, 100*a/(n*b), fb, fa/fb }'
    [ -z "${CMP_DIR}" ] || awk -v l="${L}" -v c="${CMP_LAYOUT}" -v a="${STEPC}" -v b="${STEPN}" \
        'BEGIN { printf "    %s per node vs %s: %.2fx (above 1 = faster)\n", l, c, a/b }'
done
[ -z "${failed}" ] || stage_fail "layouts that did not finish:${failed} (check the others above)"
[ ${ok} -eq 0 ] || stage_fail "energy/SCF check failed for at least one run (see above)"
echo "=== $(date) finished"
