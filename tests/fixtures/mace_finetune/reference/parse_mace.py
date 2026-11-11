#!/usr/bin/env python3
"""Read-only extraction of per-epoch MACE metrics.
Validation metrics + timestamps come from the run .log; mean training (opt) loss
per epoch comes from the *_train.txt JSON lines.
usage: parse_mace.py RUN_LABEL LOGFILE TRAIN_TXT OUT.csv"""
import csv, json, re, sys
from collections import defaultdict
from datetime import datetime

EPOCH = re.compile(
    r"^(\S+ \S+) INFO: Epoch (\d+): head: (\S+), loss=([\d.eE+-]+), "
    r"RMSE_E_per_atom=\s*([\d.]+) meV, RMSE_F=\s*([\d.]+) meV / A")

def ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")

def main(label, log, train_txt, out):
    rows = {}            # (epoch, head) -> row; later sessions overwrite earlier (restarts)
    session, last_t = 0, {}
    for line in open(log, errors="replace"):
        if "VERIFYING SETTINGS" in line:
            session += 1
            last_t = {}
            continue
        m = EPOCH.match(line)
        if not m:
            continue
        t, ep, head, loss, re_, rf = m.groups()
        t, ep = ts(t), int(ep)
        dt = (t - last_t[head]).total_seconds() if head in last_t else ""
        last_t[head] = t
        rows[(ep, head)] = dict(run=label, epoch=ep, head=head, session=session,
                                timestamp=t.isoformat(sep=" "), epoch_wall_s=dt,
                                valid_loss=float(loss), valid_rmse_e_mev_atom=float(re_),
                                valid_rmse_f_mev_A=float(rf), train_loss_mean="", train_batches="")
    # mean optimisation loss per epoch. train.txt is appended across restarts and
    # earlier attempts, so keep only the last contiguous block written for each epoch
    s, n = defaultdict(float), defaultdict(int)
    prev = None
    for line in open(train_txt):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("mode") != "opt" or d.get("epoch") is None:
            continue
        ep = d["epoch"]
        if ep != prev:          # a new block for this epoch replaces any earlier one
            s[ep], n[ep] = 0.0, 0
            prev = ep
        s[ep] += d["loss"]; n[ep] += 1
    for (ep, head), r in rows.items():
        if n[ep]:
            r["train_loss_mean"] = s[ep] / n[ep]
            r["train_batches"] = n[ep]
    out_rows = [rows[k] for k in sorted(rows)]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader(); w.writerows(out_rows)
    print(f"{label}: {len(out_rows)} epoch rows -> {out}")

if __name__ == "__main__":
    main(*sys.argv[1:5])
