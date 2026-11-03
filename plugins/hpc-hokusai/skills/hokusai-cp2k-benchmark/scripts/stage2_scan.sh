#!/bin/bash
# Stage 2 of the CP2K layout benchmark: layout scan, run by bench_job.sh.
# SCAN_INPUT (>= 10 MD steps; steps 1-2 are not timed) in each of SCAN_LAYOUTS
# (default: the facility's full-node layouts, 112x1 56x2 28x4 16x7 8x14 4x28).
# Stops at the first failed run. SCAN_TIMEOUT: seconds per run (default 240).
# Passes only if all runs exit 0, reach PROGRAM ENDED and agree in energy.
STAGE=stage2
source "${BENCH:?}/run_layouts.sh"
INP=${SCAN_INPUT:?set SCAN_INPUT in the job script}
TMO=${SCAN_TIMEOUT:-240}

for L in ${SCAN_LAYOUTS:-112x1 56x2 28x4 16x7 8x14 4x28}; do
    run_layout ${INP} ${L%x*} ${L#*x} 1 ${TMO} || stage_fail "${INP} ${L}"
done
${PY} --check "${STAGEDIR}" || stage_fail "energies of the layouts disagree"
echo "=== $(date '+%F %T') stage 2 passed; fastest layouts: $(${PY} --pick 3 "${STAGEDIR}")"
