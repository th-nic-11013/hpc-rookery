#!/bin/bash
#SBATCH --job-name=mace-mh-restart
#SBATCH --account=<PROJECT_ACCOUNT>
#SBATCH --partition=gpu-small
#SBATCH --nodes=10
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=48
#SBATCH --time=24:00:00
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err

set -euo pipefail

# ============================================================
# Environment
# ============================================================

module load miniconda/26.7.0
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

TARGET_TRAIN="$BASE/data/combined_train.extxyz"
TARGET_VALID="$BASE/data/combined_valid.extxyz"

REPLAY="$BASE/runs/replay-selection-<REPLAY_JOBID>/selected_replay.xyz"

# DFT-computed isolated-atom energies (VASP, see vasp-dft/E0s/ and dft_e0/README.md).
E0S_FILE="$BASE/dft_e0/e0s_by_Z.json"

# Point this at the checkpoints/ directory of the run being resumed
CHECKPOINTS="$BASE/run-<ORIGINAL_JOBID>/checkpoints"

# ============================================================
# Information
# ============================================================

echo "=========================================="
echo "MACE MULTIHEAD RESTART (Method 1: preselected replay)"
echo "=========================================="
echo "JOB ID       : $SLURM_JOB_ID"
echo "NODES        : $SLURM_NNODES"
echo "GPUS/NODE    : 1"
echo "TOTAL GPUS   : $SLURM_NNODES"
echo "NODE LIST    : $SLURM_JOB_NODELIST"
echo "CHECKPOINTS  : $CHECKPOINTS"
echo "REPLAY       : $REPLAY"
echo "=========================================="

# ============================================================
# Validate files
# ============================================================

for FILE in "$FOUNDATION" "$TARGET_TRAIN" "$TARGET_VALID" "$REPLAY" "$E0S_FILE"; do
    if [[ ! -f "$FILE" ]]; then
        echo "ERROR: File not found: $FILE"
        exit 1
    fi
done

if ! find "$CHECKPOINTS" -maxdepth 1 -type f -name "*.pt" | grep -q .; then
    echo "ERROR: No checkpoint files found in: $CHECKPOINTS"
    exit 1
fi

# ============================================================
# GPU validation (per node)
# ============================================================

echo
echo "=== GPU VALIDATION ==="

srun \
    --ntasks="$SLURM_NNODES" \
    --ntasks-per-node=1 \
    --gres=gpu:1 \
    bash -c '
        module load miniconda/26.7.0
        eval "$(conda shell.bash hook)"
        conda activate mace
        echo "HOST=$(hostname)"
        echo "CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
        nvidia-smi -L
    '

# ============================================================
# NCCL / GLOO / InfiniBand — resolve MASTER_ADDR from the IB interface,
# not plain hostname resolution (see 02_finetune.slurm for why).
# ============================================================

FIRST_NODE=$(scontrol show hostnames "$SLURM_JOB_NODELIST" | head -n1)
export MASTER_ADDR=$(srun --nodes=1 --ntasks=1 -w "$FIRST_NODE" \
    bash -c "ip -o -4 addr show ib0 | awk '{print \$4}' | cut -d/ -f1")
export MASTER_PORT=29500

export NCCL_SOCKET_IFNAME=ib0
export GLOO_SOCKET_IFNAME=ib0
export NCCL_IB_HCA=mlx5
export NCCL_IB_DISABLE=0
export NCCL_DEBUG=INFO

echo "MASTER_ADDR=$MASTER_ADDR"
echo "MASTER_PORT=$MASTER_PORT"

# ============================================================
# RESTART
#
# All --name/--foundation_model/hyperparameter flags below must exactly
# match 02_finetune.slurm's invocation, or the restored checkpoint won't
# line up with the model being restarted. REPLAY must point at the same
# preselected file used to produce the checkpoint being resumed.
# ============================================================

echo
echo "=========================================="
echo "RESUMING FROM LATEST CHECKPOINT"
echo "=========================================="
ls -lh "$CHECKPOINTS"

srun \
    --nodes="$SLURM_NNODES" \
    --ntasks="$SLURM_NNODES" \
    --ntasks-per-node=1 \
    --gpus-per-task=1 \
    --cpus-per-task=48 \
    --cpu-bind=cores \
    bash -c '
        module load miniconda/26.7.0
        eval "$(conda shell.bash hook)"
        conda activate mace

        export OMP_NUM_THREADS=8
        export NCCL_SOCKET_IFNAME=ib0
        export GLOO_SOCKET_IFNAME=ib0
        export NCCL_IB_HCA=mlx5
        export NCCL_IB_DISABLE=0

        python -m mace.cli.run_train \
            --name mh1_omat_pbe_meoh_litfsi_vasp_pbe_d3bj \
            --foundation_head omat_pbe \
            --foundation_model "'"$FOUNDATION"'" \
            --pt_train_file "'"$REPLAY"'" \
            --train_file "'"$TARGET_TRAIN"'" \
            --valid_file "'"$TARGET_VALID"'" \
            --energy_key energy \
            --forces_key forces \
            --atomic_numbers "[1, 3, 6, 7, 8, 9, 16]" \
            --multiheads_finetuning True \
            --filter_type_pt combinations \
            --subselect_pt fps \
            --E0s "'"$E0S_FILE"'" \
            --weight_pt 1.0 \
            --energy_weight 10.0 \
            --forces_weight 100.0 \
            --stress_weight 0.0 \
            --scheduler_patience 10 \
            --batch_size 1 \
            --valid_batch_size 1 \
            --max_num_epochs 100 \
            --ema \
            --ema_decay 0.995 \
            --amsgrad \
            --clip_grad 10.0 \
            --seed 42 \
            --patience 20 \
            --error_table PerAtomRMSE \
            --default_dtype float32 \
            --device cuda \
            --distributed \
            --launcher slurm \
            --checkpoints_dir "'"$CHECKPOINTS"'" \
            --restart_latest \
            --keep_checkpoints
    '

echo
echo "=========================================="
echo "MULTIHEAD RESTART COMPLETED"
echo "=========================================="
