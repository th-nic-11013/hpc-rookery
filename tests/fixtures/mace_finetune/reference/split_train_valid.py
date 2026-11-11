#!/usr/bin/env python3
"""Split a combined extxyz dataset into train/valid sets for MACE fine-tuning."""

import argparse
import random
from pathlib import Path

from ase.io import read, write


def main():
    parser = argparse.ArgumentParser(
        description="Randomly split an extxyz file into train/valid sets."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--train-output", required=True)
    parser.add_argument("--valid-output", required=True)
    parser.add_argument("--valid-fraction", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)

    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_file():
        raise FileNotFoundError(f"File not found: {input_path}")

    print(f"Reading {input_path} ...")
    frames = read(input_path, index=":")
    n_total = len(frames)
    print(f"Loaded {n_total} configurations.")

    indices = list(range(n_total))
    random.Random(args.seed).shuffle(indices)

    n_valid = max(1, round(n_total * args.valid_fraction))
    valid_idx = set(indices[:n_valid])

    train_frames = [f for i, f in enumerate(frames) if i not in valid_idx]
    valid_frames = [f for i, f in enumerate(frames) if i in valid_idx]

    train_output = Path(args.train_output)
    valid_output = Path(args.valid_output)
    train_output.parent.mkdir(parents=True, exist_ok=True)
    valid_output.parent.mkdir(parents=True, exist_ok=True)

    write(train_output, train_frames)
    write(valid_output, valid_frames)

    print(f"Train: {len(train_frames)} configs -> {train_output}")
    print(f"Valid: {len(valid_frames)} configs -> {valid_output}")
    print(f"Valid fraction actual: {len(valid_frames) / n_total:.4f}")


if __name__ == "__main__":
    main()
