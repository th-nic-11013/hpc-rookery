#!/bin/bash
#SBATCH --job-name=vasp-bench
#SBATCH --partition={{VASP_PARTITION}}
#SBATCH --nodes=1
#SBATCH --exclusive
#SBATCH --time=01:00:00
#
# VASP parallel-layout benchmark on one exclusive node of {{DISPLAY_NAME}}.
#
# Every layout RANKS:KPAR:NCORE runs the same input for the same fixed number
# of SCF steps (NELMIN = NELM in its INCAR) with NBANDS pinned, so the runs do
# identical work and only the parallel split differs. Fail-fast and
# cost-capped: the first failed run stops the job, every run has a timeout,
# and no run starts that could outlast the job.
#
# Layout of the bench folder (the job runs in it):
#   bench_job.sh  inputs/{INCAR,POSCAR,POTCAR[,KPOINTS]}
# Output: runs/<jobid>/<NN>-r<R>-k<KPAR>c<NCORE>/OUTCAR, and a table at the
# end of the job log.
#
# Settings come from the environment (submit_job's spec "environment"), or
# edit the defaults in the BENCHMARK SETTINGS block. Under submit_job the spec
# sets the resources; the #SBATCH lines above are for a manual sbatch only.
#
# Dry run (no VASP, no srun; each "run" copies REF_OUTCAR):
#   DRYRUN=1 REF_OUTCAR=<a finished OUTCAR with the pinned NBANDS> bash bench_job.sh
# plus DRYRUN_FAIL=<n> (run n fails) and BENCH_LIMIT_S=60 (the time guard
# refuses a run) to test the stops.
set -u

# ========================= BENCHMARK SETTINGS =========================
VASP_ENV="${VASP_ENV:-}"                 # the build's env_script (software_registry)
VASP_EXE="${VASP_EXE:-vasp_std}"         # vasp_<variant.exe>; vasp_gam only for a Gamma-only mesh
INPUT_DIR="${INPUT_DIR:-inputs}"
LAYOUTS="${LAYOUTS:-{{VASP_SCAN_LAYOUTS}}}"   # RANKS:KPAR:NCORE ..., the first is the baseline
REPEAT_BASELINE="${REPEAT_BASELINE:-1}"  # 1: run the baseline again at the end (the noise level)
RUN_TIMEOUT="${RUN_TIMEOUT:-1200}"       # s per run: ~2x the slowest expected run + 2 min
BENCH_LIMIT_S="${BENCH_LIMIT_S:-3600}"   # must equal the job's duration in seconds
CORES_PER_NODE="${CORES_PER_NODE:-{{VASP_CORES_PER_NODE}}}"
SRUN_MPI="${SRUN_MPI:-{{VASP_SRUN_MPI}}}"
DRYRUN="${DRYRUN:-0}"
REF_OUTCAR="${REF_OUTCAR:-}"
DRYRUN_FAIL="${DRYRUN_FAIL:-0}"
# ======================================================================

START=$(date +%s)
die() { echo "BENCH STOP: $*"; [ -n "${SUMMARY:-}" ] && summary; exit 1; }

# The value of an INCAR tag (comments stripped, ';' splits tags), or empty.
tag() {
    sed -e 's/[#!].*//' "$INPUT_DIR/INCAR" | tr ';' '\n' |
        awk -F= -v t="$1" '{k=toupper($1); gsub(/[ \t]/, "", k)}
                           k == t {v=$2; gsub(/^[ \t]+/, "", v); split(v, a, /[ \t]+/); last=a[1]}
                           END {print last}'
}

# ---- the input -------------------------------------------------------------
for f in INCAR POSCAR POTCAR; do
    [ -s "$INPUT_DIR/$f" ] || die "missing $INPUT_DIR/$f"
done
[ -s "$INPUT_DIR/KPOINTS" ] || [ -n "$(tag KSPACING)" ] || die "no KPOINTS and no KSPACING"
NBANDS=$(tag NBANDS)
[ -n "$NBANDS" ] || die "pin NBANDS in the INCAR: VASP rounds it to the band groups, so unpinned runs would do different work"
NELM=$(tag NELM); NELMIN=$(tag NELMIN)
[ -n "$NELM" ] && [ "$NELM" = "$NELMIN" ] ||
    die "set NELMIN = NELM (e.g. 10) so every layout runs the same number of SCF steps (now NELM=${NELM:-unset}, NELMIN=${NELMIN:-unset})"
NSW=$(tag NSW)
[ -z "$NSW" ] || [ "$NSW" = 0 ] || die "NSW=$NSW: the benchmark times one ionic step; set NSW = 0"
if sed -e 's/[#!].*//' "$INPUT_DIR/INCAR" | grep -Eiq '(KPAR|NCORE|NPAR)[^;]*;|;[^;]*(KPAR|NCORE|NPAR)'; then
    die "put KPAR, NCORE and NPAR on lines of their own in the INCAR: each run replaces those lines"
fi

# ---- the layouts, all checked before anything runs ---------------------------
read -r -a LIST <<< "$LAYOUTS"
[ "${#LIST[@]}" -gt 0 ] || die "LAYOUTS is empty"
for L in "${LIST[@]}"; do
    IFS=: read -r R K C <<< "$L"
    case "$R:$K:$C" in *[!0-9:]*|:*|*::*|*:) die "layout '$L' is not RANKS:KPAR:NCORE" ;; esac
    [ "$R" -ge 1 ] && [ "$K" -ge 1 ] && [ "$C" -ge 1 ] || die "layout '$L': every number must be >= 1"
    [ "$R" -le "$CORES_PER_NODE" ] || die "layout '$L': $R ranks > $CORES_PER_NODE cores"
    [ $((R % K)) -eq 0 ] || die "layout '$L': KPAR $K does not divide $R ranks"
    [ $((R / K % C)) -eq 0 ] || die "layout '$L': NCORE $C does not divide $((R / K)) ranks per k-point group"
    G=$((R / K / C))
    [ $((NBANDS % G)) -eq 0 ] || die "layout '$L': $G band groups do not divide NBANDS $NBANDS (VASP would change NBANDS)"
    [ "$R" -eq "$CORES_PER_NODE" ] ||
        echo "note: layout $L leaves $((CORES_PER_NODE - R)) cores idle; launch_plan uses only full-node records"
done
[ "$REPEAT_BASELINE" = 1 ] && LIST+=("${LIST[0]}")

# ---- the build ---------------------------------------------------------------
if [ "$DRYRUN" = 1 ]; then
    [ -s "$REF_OUTCAR" ] || die "DRYRUN=1 needs REF_OUTCAR, a finished OUTCAR"
    REF_OUTCAR=$(cd "$(dirname "$REF_OUTCAR")" && pwd)/$(basename "$REF_OUTCAR")
    srun() { echo "srun called in a dry run"; exit 3; }   # never reached; a guard
    JOB="dryrun-$(date +%Y%m%d-%H%M%S)-$$"
else
    [ -n "$VASP_ENV" ] || die "set VASP_ENV to the build's env_script"
    # shellcheck disable=SC1090
    source "$VASP_ENV" || die "sourcing $VASP_ENV failed"
    command -v "$VASP_EXE" > /dev/null || die "$VASP_EXE is not on PATH after sourcing $VASP_ENV"
    JOB="${SLURM_JOB_ID:-manual-$(date +%Y%m%d-%H%M%S)}"
fi
export OMP_NUM_THREADS=1
RUNS="runs/$JOB"
[ ! -e "$RUNS" ] || die "$RUNS exists already: results are never mixed"
mkdir -p "$RUNS" || die "cannot create $RUNS"
echo "VASP benchmark $JOB: NBANDS $NBANDS, $NELM SCF steps per run, layouts: ${LIST[*]}"

ROWS=()
SUMMARY=1
summary() {
    echo
    echo "layout            ranks KPAR NCORE groups NBANDS steps loop_s  s/step  speedup TOTEN_eV         dE_meV"
    local base_s="" base_e="" row
    for row in "${ROWS[@]}"; do
        set -- $row
        local name=$1 r=$2 k=$3 c=$4 g=$5 nb=$6 st=$7 lp=$8 e=$9
        local sps="-" sp="-" de="-"
        if [ "$lp" != "-" ] && [ "$st" != "-" ] && [ "$st" -gt 0 ]; then
            sps=$(awk -v a="$lp" -v b="$st" 'BEGIN {printf "%.3f", a / b}')
            [ -n "$base_s" ] || base_s=$sps
            sp=$(awk -v a="$base_s" -v b="$sps" 'BEGIN {printf "%.2f", a / b}')
        fi
        if [ "$e" != "-" ]; then
            [ -n "$base_e" ] || base_e=$e
            de=$(awk -v a="$e" -v b="$base_e" 'BEGIN {printf "%.3f", (a - b) * 1000}')
        fi
        printf '%-17s %5s %4s %5s %6s %6s %5s %7s %7s %7s %-16s %s\n' \
            "$name" "$r" "$k" "$c" "$g" "$nb" "$st" "$lp" "$sps" "$sp" "$e" "$de"
    done
    echo "speedup and dE are against the first row (the baseline); s/step = loop_s / steps"
}

# ---- the runs -----------------------------------------------------------------
i=0
for L in "${LIST[@]}"; do
    IFS=: read -r R K C <<< "$L"
    i=$((i + 1))
    name=$(printf '%02d-r%d-k%dc%d' "$i" "$R" "$K" "$C")
    left=$((BENCH_LIMIT_S - ($(date +%s) - START)))
    [ "$left" -gt $((RUN_TIMEOUT + 90)) ] ||
        die "$left s of job time left: a run of up to $RUN_TIMEOUT s (+90 s slack) would not fit; $name not started"
    d="$RUNS/$name"
    mkdir "$d" || die "cannot create $d"
    cp "$INPUT_DIR"/INCAR "$INPUT_DIR"/POSCAR "$INPUT_DIR"/POTCAR "$d"/
    [ -s "$INPUT_DIR/KPOINTS" ] && cp "$INPUT_DIR/KPOINTS" "$d"/
    sed -i -E '/^[[:space:]]*(KPAR|NCORE|NPAR)[[:space:]]*=/Id' "$d/INCAR"
    printf 'KPAR   = %s\nNCORE  = %s\n' "$K" "$C" >> "$d/INCAR"
    echo "run $name: $R ranks, KPAR $K, NCORE $C ($((R / K / C)) band groups), timeout $RUN_TIMEOUT s"
    if [ "$DRYRUN" = 1 ]; then
        if [ "$DRYRUN_FAIL" = "$i" ]; then
            rc=1
        else
            cp "$REF_OUTCAR" "$d/OUTCAR"; rc=$?
        fi
    else
        (cd "$d" && timeout "$RUN_TIMEOUT" srun $SRUN_MPI --nodes=1 --ntasks="$R" --cpus-per-task=1 \
            --cpu-bind=cores "$VASP_EXE" > vasp.out 2>&1)
        rc=$?
    fi
    out="$d/OUTCAR"
    steps=$(grep -c 'LOOP:' "$out" 2>/dev/null); steps=${steps:-0}
    loop=$(grep 'LOOP+:' "$out" 2>/dev/null | tail -1 | awk '{print $NF}')
    nb=$(grep -m1 'NBANDS=' "$out" 2>/dev/null | sed -E 's/.*NBANDS=[[:space:]]*([0-9]+).*/\1/')
    toten=$(grep 'free  energy   TOTEN' "$out" 2>/dev/null | tail -1 | awk '{print $5}')
    ROWS+=("$name $R $K $C $((R / K / C)) ${nb:--} $steps ${loop:--} ${toten:--}")
    [ "$rc" -eq 0 ] || die "$name exited $rc$([ "$rc" -eq 124 ] && echo ' (timeout)'); see $d/vasp.out"
    grep -q 'General timing and accounting' "$out" 2>/dev/null || die "$name: OUTCAR is unfinished"
    [ "$nb" = "$NBANDS" ] || die "$name ran with NBANDS ${nb:-?}, not the pinned $NBANDS"
    [ "$steps" = "$NELM" ] || echo "warning: $name ran $steps SCF steps, not $NELM"
done
summary
echo "BENCH DONE: ${#ROWS[@]} runs in $(( $(date +%s) - START )) s"
