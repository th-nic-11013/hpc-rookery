#!/bin/bash
#SBATCH --job-name=mace-replay-select
#SBATCH --partition=<debug_partition>
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:00:00
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err

set -euo pipefail

# ============================================================
# Environment
# ============================================================

module load <conda_module>
eval "$(conda shell.bash hook)"
conda activate mace

export OMP_NUM_THREADS=8
export OMP_PLACES=cores
export OMP_PROC_BIND=close

# ============================================================
# Paths
# ============================================================

BASE="$SLURM_SUBMIT_DIR"

FOUNDATION="/home/user/project3/foundation-models/mace-mh-1.model"
REPLAY="/home/user/project3/foundation-models/replay-data-mh-1-omat-pbe.xyz"
TARGET_TRAIN="$BASE/data/combined_train.extxyz"

SCRIPT="$BASE/scripts/select_replay.py"

WORKDIR="$BASE/runs/replay-selection-${SLURM_JOB_ID}"
SELECTED="$WORKDIR/selected_replay.xyz"

mkdir -p "$WORKDIR"

# ============================================================
# Information
# ============================================================

echo "=========================================="
echo "MACE REPLAY SELECTION"
echo "=========================================="
echo "JOB ID       : $SLURM_JOB_ID"
echo "NODE         : $SLURM_JOB_NODELIST"
echo "WORKDIR      : $WORKDIR"
echo "FOUNDATION   : $FOUNDATION"
echo "REPLAY       : $REPLAY"
echo "TARGET       : $TARGET_TRAIN"
echo "OUTPUT       : $SELECTED"
echo "=========================================="

# ============================================================
# Validate files
# ============================================================

for FILE in "$FOUNDATION" "$REPLAY" "$TARGET_TRAIN" "$SCRIPT"; do
    if [[ ! -f "$FILE" ]]; then
        echo "ERROR: File not found: $FILE"
        exit 1
    fi
done

# ============================================================
# GPU validation
# ============================================================

echo
echo "=== GPU VALIDATION ==="
nvidia-smi -L
nvidia-smi --query-gpu=index,name,memory.total,compute_cap --format=csv

# ============================================================
# Replay selection
# ============================================================

echo
echo "=== SELECTING REPLAY CONFIGURATIONS ==="

python "$SCRIPT" \
    --replay "$REPLAY" \
    --target "$TARGET_TRAIN" \
    --model "$FOUNDATION" \
    --output "$SELECTED" \
    --num-samples 10000 \
    --head-pt pt_head \
    --head-ft default \
    --filter-atomic-numbers-pt "1,3,6,7,8,9,16" \
    --device cuda

# ============================================================
# Validate output
# ============================================================

if [[ ! -s "$SELECTED" ]]; then
    echo "ERROR: Selected replay file is empty."
    exit 1
fi

echo
echo "=========================================="
echo "REPLAY SELECTION COMPLETED"
echo "=========================================="
echo "Selected file: $SELECTED"
ls -lh "$SELECTED"
echo "=========================================="
