#!/bin/bash
# Stage 3 of the CP2K layout benchmark: validation, run by bench_job.sh.
# VALIDATE_INPUT (a larger system) with the VALIDATE_PICK (default 3) fastest layouts
# of stage 2, picked from its results, best first, plus a repeat of the best one.
# Stops at the first failed run. VALIDATE_TIMEOUT: seconds per run (default 360).
# VALIDATE_INPUT=none (the default) skips the stage; it then passes.
STAGE=stage3
source "${BENCH:?}/run_layouts.sh"
INP=${VALIDATE_INPUT:-none}
TMO=${VALIDATE_TIMEOUT:-360}
if [ "${INP}" = "none" ]; then
    echo "=== $(date '+%F %T') stage 3 skipped (VALIDATE_INPUT=none)"
    exit 0
fi

S2=${BENCH}/runs/${SLURM_JOB_ID}/stage2
LAYOUTS=$(${PY} --pick ${VALIDATE_PICK:-3} "${S2}") || stage_fail "no usable layout in ${S2}"
echo "=== layouts picked from stage 2, best first: ${LAYOUTS}"
set -- ${LAYOUTS}

for L in "$@"; do
    run_layout ${INP} ${L%x*} ${L#*x} 1 ${TMO} || stage_fail "${INP} ${L}"
done
run_layout ${INP} ${1%x*} ${1#*x} 2 ${TMO} || stage_fail "${INP} ${1} repeat"
${PY} --check "${STAGEDIR}" || stage_fail "energies of the layouts disagree"
echo "=== $(date '+%F %T') stage 3 passed"
