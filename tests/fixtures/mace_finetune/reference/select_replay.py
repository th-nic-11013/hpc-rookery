#!/usr/bin/env python3
"""Select MACE replay configurations for multihead fine-tuning (Method 1)."""

import argparse
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Select MACE Method-1 replay configurations."
    )

    parser.add_argument("--replay", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)

    parser.add_argument("--num-samples", type=int, default=30000)
    parser.add_argument("--head-pt", default="pt_head")
    parser.add_argument("--head-ft", default="default")
    parser.add_argument("--weight-pt", type=float, default=1.0)
    parser.add_argument(
        "--filter-atomic-numbers-pt",
        default="1,6,7,8,9,16,17",
        help="Comma-separated atomic numbers to keep from the pretraining pool.",
    )
    parser.add_argument("--device", default="cuda")

    args = parser.parse_args()

    replay = Path(args.replay)
    target = Path(args.target)
    model = Path(args.model)
    output = Path(args.output)

    for path in [replay, target, model]:
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")

    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python",
        "-m",
        "mace.cli.fine_tuning_select",
        "--configs_pt",
        str(replay),
        "--configs_ft",
        str(target),
        "--num_samples",
        str(args.num_samples),
        "--subselect",
        "fps",
        "--model",
        str(model),
        "--output",
        str(output),
        "--filtering_type",
        "combinations",
        "--filter_atomic_numbers_pt",
        args.filter_atomic_numbers_pt,
        "--head_pt",
        args.head_pt,
        "--head_ft",
        args.head_ft,
        "--weight_pt",
        str(args.weight_pt),
        "--device",
        args.device,
    ]

    print("\nRunning:\n")
    print(" ".join(cmd))
    print()

    subprocess.run(cmd, check=True)

    if not output.is_file():
        raise RuntimeError(f"MACE completed but output was not created: {output}")

    print("\nReplay selection completed.")
    print(f"Selected replay: {output}")


if __name__ == "__main__":
    main()
