#!/bin/bash
# Restartable fleet driver: works through many independent calculations
# ("frames") of a campaign inside one Slurm job, one lane per node. The
# folder layout below is what campaign_status reads.
#
#   <FLEET_DIR>/<Group>/<frame>/            work to do (group: [A-Z]*, frame: f[0-9][0-9][0-9])
#   <FLEET_DIR>/<Group>/<shared files>      e.g. POTCAR, copied into each frame before its run
#   <FLEET_DIR>/completed/<Group>/<frame>/  done (FLEET_CHECK passed)
#   <FLEET_DIR>/failed/<Group>/<frame>/     failed MAX_ATTEMPTS times; REASON says why
#   <FLEET_DIR>/scf_hold/<Group>/<frame>/   parked by hand; this driver never looks there
#
# Per frame: a lane claims it with an atomic `mkdir <frame>/.running`
# (its job id in .running/jobid), so lanes load-balance and two jobs on one
# campaign are safe; .attempts counts tries, .lastjob names the job that tried
# last (one try per frame per job: a fast failure is not retried at once by the
# next lane), and the run's output goes to $FLEET_LOG (old ones kept as
# $FLEET_LOG.<n>). A missing shared file leaves the flag NEEDS_<file>.
#
# Walltime: with `--signal=B:USR1@600` the USR1 trap stops the runs and
# leaves the interrupted frames for the next submission without counting the
# try; no frame starts with less than JOB_EST_SECONDS left. Resubmit in the
# same folder to continue: retired frames are no longer in the work glob, and
# claims left by a job that is gone are removed at start.
#
# Run it as `exec bash run_fleet.sh` (exec: the USR1 must reach this script,
# not a wrapper shell), with FLEET_DIR and FLEET_RUN in the environment:
#
#   FLEET_DIR      campaign root (default: $SLURM_SUBMIT_DIR, else $PWD)
#   FLEET_RUN      command that runs one frame, from inside its folder, e.g.
#                  'srun -N1 -n "$FLEET_RANKS" -w "$FLEET_NODE" --exact vasp_std'
#                  or an mpirun with -hostfile "$FLEET_HOSTFILE". Required.
#   FLEET_CHECK    command (in the frame folder) that succeeds when the frame
#                  holds a usable result; default: a VASP single point
#                  (forces and TOTEN in OUTCAR, F= in OSZICAR, SCF steps < NELM)
#   FLEET_SHARED   files copied from the group folder into the frame before
#                  each run and removed when it retires (default: POTCAR)
#   FLEET_CLEAN    large files removed when a frame retires
#                  (default: CHG CHGCAR WAVECAR PROCAR EIGENVAL DOSCAR)
#   FLEET_GZIP     files gzipped when a frame retires (default: vasprun.xml)
#   FLEET_LOG      the run's output file (default: vasp.out)
#   RANKS_PER_LANE ranks per frame (default: $SLURM_NTASKS_PER_NODE, else 1)
#   MAX_ATTEMPTS   tries before a frame is parked in failed/ (default 2)
#   JOB_EST_SECONDS  do not start a frame with less walltime left (default 7200)
#   order.txt      optional, in FLEET_DIR: frame folders in run order, one per line
#
#SBATCH --output=logs/fleet_%j.log
#SBATCH --error=logs/fleet_%j.err
#SBATCH --signal=B:USR1@600

set -u   # no -e: one bad frame must not stop its lane; no pipefail: see fleet_check
shopt -s nullglob

cd "${FLEET_DIR:-${SLURM_SUBMIT_DIR:-$PWD}}" || exit 1
BASE=$PWD
GROUP_GLOB='[A-Z]*'
FRAME_GLOB='f[0-9][0-9][0-9]'
RANKS_PER_LANE=${RANKS_PER_LANE:-${SLURM_NTASKS_PER_NODE:-1}}
MAX_ATTEMPTS=${MAX_ATTEMPTS:-2}
JOB_EST_SECONDS=${JOB_EST_SECONDS:-7200}
FLEET_SHARED=${FLEET_SHARED-POTCAR}
FLEET_CLEAN=${FLEET_CLEAN-CHG CHGCAR WAVECAR PROCAR EIGENVAL DOSCAR}
FLEET_GZIP=${FLEET_GZIP-vasprun.xml}
FLEET_LOG=${FLEET_LOG:-vasp.out}
JOB=${SLURM_JOB_ID:-local}
if [[ -z "${FLEET_RUN:-}" ]]; then
    echo "[$(date +%FT%T)] FLEET_RUN is not set: nothing to run each frame with" >&2
    exit 1
fi
mkdir -p completed failed logs hostfiles
STOPFILE="$BASE/logs/.stop_$JOB"
rm -f "$STOPFILE"

log() { echo "[$(date +%FT%T)] $*"; }

if [[ -n "${SLURM_JOB_END_TIME:-}" ]]; then
    DEADLINE=$SLURM_JOB_END_TIME
elif [[ -n "${SLURM_JOB_ID:-}" ]]; then
    DEADLINE=$(date -d "$(scontrol show job "$SLURM_JOB_ID" | grep -oP 'EndTime=\K\S+')" +%s)
else
    DEADLINE=$(( $(date +%s) + 10 * 86400 ))   # outside Slurm: no limit
fi

if [[ -n "${SLURM_JOB_NODELIST:-${SLURM_NODELIST:-}}" ]]; then
    mapfile -t NODES < <(scontrol show hostnames "${SLURM_JOB_NODELIST:-$SLURM_NODELIST}")
else
    NODES=("$(hostname)")
fi
LANES=${#NODES[@]}
for i in "${!NODES[@]}"; do
    echo "${NODES[$i]}:${RANKS_PER_LANE}" > "hostfiles/lane$((i+1)).hosts"
done

# --- remove claims left by jobs that are no longer in the queue --------------
live=$(squeue -h -u "$USER" -o %A 2>/dev/null)
for lock in $GROUP_GLOB/$FRAME_GLOB/.running; do
    [[ -e "$lock" ]] || continue
    owner=$(cat "$lock/jobid" 2>/dev/null)
    if [[ -z "$owner" ]] || ! grep -qx "$owner" <<<"$live"; then
        log "removing stale claim $lock (job ${owner:-unknown})"
        rm -rf "$lock"
    fi
done

# --- does the frame hold a usable result? -------------------------------------
# Judged by content, not by the exit status: a code can die writing a trailing
# file after the result is complete. (No pipefail: `grep -q` closing a pipe
# early would count as a failure.)
vasp_check() {
    [[ -f OUTCAR && -f OSZICAR ]] || return 1
    grep -q "TOTAL-FORCE" OUTCAR || return 1
    grep -qE "free +energy +TOTEN" OUTCAR || return 1
    grep -q "F=" OSZICAR || return 1
    local nelm steps
    nelm=$(awk -F= 'toupper($1) ~ /^ *NELM *$/ {gsub(/[^0-9]/, "", $2); print $2; exit}' INCAR)
    nelm=${nelm:-60}
    steps=$(grep -cE "^(DAV|RMM|CG):" OSZICAR)
    [[ "$steps" -lt "$nelm" ]]
}
fleet_check() {
    if [[ -n "${FLEET_CHECK:-}" ]]; then (cd "$1" && eval "$FLEET_CHECK"); else (cd "$1" && vasp_check); fi
}

# The claim is removed only AFTER the folder has left the work glob; removing
# it first would let another lane claim a finished frame again.
retire() {
    local d=$1 dest=$2 group=${1%%/*} frame=${1##*/} f
    for f in $FLEET_SHARED $FLEET_CLEAN; do rm -f "$d/$f"; done
    for f in $FLEET_GZIP; do [[ -f "$d/$f" ]] && gzip -f "$d/$f"; done
    mkdir -p "$dest/$group"
    mv "$d" "$dest/$group/" || return 1
    rm -rf "$dest/$group/$frame/.running"
}

release() { local f; for f in $FLEET_SHARED; do rm -f "$1/$f"; done; rm -rf "$1/.running"; }

# --- walltime: stop the runs, leave their frames for the next submission ------
# Lanes are separate processes, so the signal is relayed through a file.
# Every process under the lanes is stopped; the lanes themselves stay, to
# release their frames.
kill_tree() { local c; for c in $(pgrep -P "$1"); do kill_tree "$c"; done; kill -TERM "$1" 2>/dev/null; }
on_usr1() {
    touch "$STOPFILE"
    log "USR1: walltime nearly up, winding down"
    local p c
    for p in $(jobs -p); do for c in $(pgrep -P "$p"); do kill_tree "$c"; done; done
}
trap on_usr1 USR1

lane() {
    local id=$1 runs d group attempt rc f missing
    export FLEET_HOSTFILE="$BASE/hostfiles/lane$1.hosts" FLEET_NODE=${NODES[$(( $1 - 1 ))]}
    export FLEET_RANKS=$RANKS_PER_LANE
    if [[ -f "$BASE/order.txt" ]]; then
        mapfile -t runs < <(grep -v '^ *#' "$BASE/order.txt" | grep -v '^ *$')
    else
        runs=($GROUP_GLOB/$FRAME_GLOB)
    fi
    for d in "${runs[@]}"; do
        [[ -e "$STOPFILE" ]] && { log "lane $id: stopping"; return 0; }
        [[ -d "$d" ]] || continue
        mkdir "$d/.running" 2>/dev/null || continue
        echo "$JOB" > "$d/.running/jobid"

        if fleet_check "$d"; then                # finished before, never moved
            log "lane $id: $d already complete, moving"
            retire "$d" completed; continue
        fi
        group=${d%%/*}
        missing=""
        for f in $FLEET_SHARED; do [[ -f "$group/$f" ]] || missing="$f"; done
        if [[ -n "$missing" ]]; then
            touch "$d/NEEDS_$missing"; release "$d"; continue
        fi
        if (( $(date +%s) + JOB_EST_SECONDS > DEADLINE )); then
            log "lane $id: not enough walltime left, stopping"
            release "$d"; return 0
        fi
        # one try per frame per job
        if [[ "$(cat "$d/.lastjob" 2>/dev/null)" == "$JOB" ]]; then
            release "$d"; continue
        fi
        echo "$JOB" > "$d/.lastjob"
        attempt=$(( $(cat "$d/.attempts" 2>/dev/null || echo 0) + 1 ))
        echo "$attempt" > "$d/.attempts"
        [[ -f "$d/$FLEET_LOG" ]] && mv "$d/$FLEET_LOG" "$d/$FLEET_LOG.$((attempt - 1))"
        for f in $FLEET_SHARED; do cp "$group/$f" "$d/$f"; done
        rm -f "$d"/NEEDS_*

        log "lane $id: running $d (attempt $attempt)"
        ( cd "$d" && eval "$FLEET_RUN" ) > "$d/$FLEET_LOG" 2>&1 < /dev/null
        rc=$?

        if fleet_check "$d"; then
            log "lane $id: $d OK (rc=$rc)"
            retire "$d" completed
        elif [[ -e "$STOPFILE" ]]; then
            log "lane $id: $d interrupted, leaving for the next submission"
            echo $((attempt - 1)) > "$d/.attempts"   # walltime is not a failure
            release "$d"; return 0
        elif (( attempt >= MAX_ATTEMPTS )); then
            log "lane $id: $d FAILED $attempt times, parking in failed/"
            echo "rc=$rc after $attempt attempts; see $FLEET_LOG*" > "$d/REASON"
            retire "$d" failed
        else
            log "lane $id: $d FAILED (rc=$rc), will retry in the next submission"
            release "$d"
        fi
    done
}

left() { local a=($GROUP_GLOB/$FRAME_GLOB); echo ${#a[@]}; }
log "starting: $(left) frames to do, $LANES lanes x $RANKS_PER_LANE ranks on ${NODES[*]}"
for i in $(seq 1 "$LANES"); do
    lane "$i" &
done
# A trapped USR1 makes `wait` return early; keep waiting so lanes can clean up.
while [[ -n "$(jobs -rp)" ]]; do wait; done
rm -f "$STOPFILE"
done_n=(completed/$GROUP_GLOB/$FRAME_GLOB); fail_n=(failed/$GROUP_GLOB/$FRAME_GLOB)
log "done: ${#done_n[@]} completed, ${#fail_n[@]} failed, $(left) left"
(( $(left) > 0 )) && log "submit again in $BASE to continue"
exit 0
