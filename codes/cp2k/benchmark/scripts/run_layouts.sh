# Helper for the CP2K layout benchmark; sourced by the stage scripts, do not run it.
# Needs from the job script: BENCH (the benchmark folder, holding inputs/ and these
# scripts), SLURM_JOB_ID, BENCH_START (epoch s when the job started) and
# BENCH_LIMIT_S (the job's --time in seconds); from the stage script: STAGE.
#
#   run_layout <input.inp> <ranks> <threads> <rep> <timeout_s>
# Runs one CP2K step in ${BENCH}/runs/${SLURM_JOB_ID}/${STAGE}/<input>_<R>x<T>_r<rep>/
# and appends "input ranks threads rep exit wall_s" to status.tsv in the stage folder.
# Returns 0 only if srun exited 0 and cp2k.out has PROGRAM ENDED. 124: the timeout
# killed it. 125: not started, because the job had too little time left for it.
# Multi-node jobs export BENCH_NODES=N: the R ranks are then spread evenly, R/N per
# node (R must be a multiple of N), with the same per-node binding.
#
#   stage_fail <message>
# Ends the stage with exit 1; the job script then stops before the next stage.

BENCH=${BENCH:?export BENCH (the benchmark folder) in the job script}
STAGEDIR=${BENCH}/runs/${SLURM_JOB_ID:?run inside the Slurm job}/${STAGE:?set STAGE first}
PY="{{CP2K_PYTHON}} -I ${BENCH}/parse_bench.py --cores={{CP2K_CORES_PER_NODE}}"
mkdir -p "${STAGEDIR}"
[ -f "${STAGEDIR}/status.tsv" ] || printf "input\tranks\tthreads\trep\texit\twall_s\n" > "${STAGEDIR}/status.tsv"

stage_fail() {
    echo "=== $(date '+%F %T') ${STAGE} FAILED: $*"
    exit 1
}

run_layout() {
    local inp=$1 R=$2 T=$3 rep=$4 tmo=$5
    local name
    name=$(basename "${inp}" .inp)
    local dir=${STAGEDIR}/${name}_${R}x${T}_r${rep}
    local t0 t1 status left
    t0=$(date +%s)

    # never start a run that could outlast the job: timeout + 30 s kill grace + 60 s margin
    left=$(( BENCH_LIMIT_S - (t0 - BENCH_START) ))
    if [ ${left} -lt $(( tmo + 90 )) ]; then
        echo "=== ${name} ${R}x${T} r${rep} NOT STARTED: ${left} s of job time left, run may need $(( tmo + 90 )) s"
        printf "%s\t%s\t%s\t%s\t125\t0\n" "${name}" "${R}" "${T}" "${rep}" >> "${STAGEDIR}/status.tsv"
        return 125
    fi

    local nodeopt=""
    if [ "${BENCH_NODES:-1}" -gt 1 ]; then
        [ $(( R % BENCH_NODES )) -eq 0 ] || { echo "=== ${R} ranks do not divide over ${BENCH_NODES} nodes"; return 1; }
        nodeopt="--nodes=${BENCH_NODES} --ntasks-per-node=$(( R / BENCH_NODES ))"
    fi

    mkdir -p "${dir}" && cp "${BENCH}/inputs/${inp}" "${dir}/" || return 1
    echo "=== $(date '+%F %T') ${name}: ${R} ranks x ${T} threads${nodeopt:+ (${nodeopt})}, rep ${rep}, timeout ${tmo} s"
    ( cd "${dir}" &&
      OMP_NUM_THREADS=${T} OMP_PLACES=cores OMP_PROC_BIND=close \
      timeout --signal=TERM --kill-after=30 "${tmo}" \
      srun {{CP2K_SRUN_MPI}} --ntasks="${R}" --cpus-per-task="${T}" ${nodeopt} \
           --cpu-bind=verbose,cores --distribution=block:block \
           cp2k.psmp -i "${inp}" -o cp2k.out 2> srun.err )
    status=$?
    t1=$(date +%s)
    if [ ${status} -eq 0 ] && ! grep -q "PROGRAM ENDED AT" "${dir}/cp2k.out" 2> /dev/null; then
        echo "    WARNING: exit 0 but no PROGRAM ENDED in ${dir}/cp2k.out"
        status=1
    fi
    printf "%s\t%s\t%s\t%s\t%s\t%s\n" "${name}" "${R}" "${T}" "${rep}" "${status}" "$((t1 - t0))" \
        >> "${STAGEDIR}/status.tsv"

    echo "    exit ${status}, wall $((t1 - t0)) s"
    grep -m 3 "cpu-bind=MASK" "${dir}/srun.err" | sed 's/^/    /'
    grep -E "Number of threads for this process|Total number of message passing" "${dir}/cp2k.out" \
        | sort -u | sed 's/^/    /'
    return ${status}
}
