#!/bin/bash
#SBATCH --job-name=mace-scratch
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

export OMP_NUM_THREADS=48
export OMP_PLACES=cores
export OMP_PROC_BIND=close

# ============================================================
# Paths
#
# No FOUNDATION or REPLAY here: this trains a fresh model from randomly
# initialized weights on the target dataset only, so multihead
# fine-tuning/replay is not applicable.
# ============================================================

BASE="$SLURM_SUBMIT_DIR"

TARGET_TRAIN="$BASE/data/combined_train.extxyz"
TARGET_VALID="$BASE/data/combined_valid.extxyz"

# DFT-computed isolated-atom energies (VASP PAW, see dft_e0/README.md), keyed
# by atomic number. Used instead of --E0s average/foundation since there is
# no foundation model here and
E0S_FILE="$BASE/dft_e0/e0s_by_Z.json"

WORKDIR="$SLURM_SUBMIT_DIR/run-${SLURM_JOB_ID}"
CHECKPOINTS="$WORKDIR/checkpoints"
mkdir -p "$WORKDIR" "$CHECKPOINTS"

# ============================================================
# Information
# ============================================================

echo "=========================================="
echo "MACE TRAINING FROM SCRATCH (no foundation model)"
echo "=========================================="
echo "JOB ID       : $SLURM_JOB_ID"
echo "NODES        : $SLURM_NNODES"
echo "GPUS/NODE    : 1"
echo "TOTAL GPUS   : $SLURM_NNODES"
echo "NODE LIST    : $SLURM_JOB_NODELIST"
echo "WORKDIR      : $WORKDIR"
echo "E0S_FILE     : $E0S_FILE"
echo "=========================================="

# ============================================================
# Validate files
# ============================================================

for FILE in "$TARGET_TRAIN" "$TARGET_VALID" "$E0S_FILE"; do
    if [[ ! -f "$FILE" ]]; then
        echo "ERROR: File not found: $FILE"
        exit 1
    fi
done

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
        python -c "import torch; print(\"CUDA available:\", torch.cuda.is_available())"
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
# MACE training from scratch
#
# atomic_numbers: H, Li, C, N, O, F, S (1,3,6,7,8,9,16) - covers methanol
# (C,H,O) + LiTFSI (Li,N,S,O,C,F). Must cover every element in the target
# dataset.
#
# E0s: DFT-computed isolated-atom energies from dft_e0/e0s_by_Z.json
# (VASP PAW, see dft_e0/README.md), not "average" or "foundation" — no
# foundation model exists here, and average-from-dataset E0s are less
# accurate than the already-computed DFT reference values.
#
# model/hidden_irreps/r_max/num_interactions: MACE-MPA-0 medium's
# architecture, since we still want a comparable model size/capacity even
# though weights start random. Adjust if a different capacity is desired.
# ============================================================

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

        export OMP_NUM_THREADS=48
        export NCCL_SOCKET_IFNAME=ib0
        export GLOO_SOCKET_IFNAME=ib0
        export NCCL_IB_HCA=mlx5
        export NCCL_IB_DISABLE=0

        python -m mace.cli.run_train \
            --name scratch_meoh_litfsi_vasp_pbe_d3bj \
            --train_file "'"$TARGET_TRAIN"'" \
            --valid_file "'"$TARGET_VALID"'" \
            --energy_key energy \
            --forces_key forces \
            --atomic_numbers "[1, 3, 6, 7, 8, 9, 16]" \
            --model MACE \
            --hidden_irreps "128x0e + 128x1o" \
            --r_max 6.0 \
            --num_interactions 2 \
            --E0s "'"$E0S_FILE"'" \
            --energy_weight 10.0 \
            --forces_weight 100.0 \
            --stress_weight 0.0 \
            --lr 0.01 \
            --lr_factor 0.5 \
            --scheduler_patience 10 \
            --batch_size 4 \
            --valid_batch_size 4 \
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
echo "TRAINING FROM SCRATCH COMPLETED"
echo "=========================================="
