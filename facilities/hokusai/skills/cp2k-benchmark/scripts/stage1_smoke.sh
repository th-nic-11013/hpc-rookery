#!/bin/bash
# Stage 1 of the CP2K layout benchmark: smoke test, run by bench_job.sh.
# SMOKE_INPUT (a short version of the scan input, about 3 MD steps) in each of
# SMOKE_LAYOUTS (default "28x4 8x14") on the job's node: checks the srun step
# geometry, binding, the CP2K environment and that the input runs at all.
# SMOKE_TIMEOUT: seconds per run (default 120).
# Passes only if all runs exit 0, reach PROGRAM ENDED and agree in energy.
STAGE=stage1
source "${BENCH:?}/run_layouts.sh"
INP=${SMOKE_INPUT:?set SMOKE_INPUT in the job script}
TMO=${SMOKE_TIMEOUT:-120}

for L in ${SMOKE_LAYOUTS:-28x4 8x14}; do
    run_layout ${INP} ${L%x*} ${L#*x} 1 ${TMO} || stage_fail "${INP} ${L}"
done
${PY} --check "${STAGEDIR}" || stage_fail "energies of the layouts disagree"
echo "=== $(date '+%F %T') stage 1 passed"
