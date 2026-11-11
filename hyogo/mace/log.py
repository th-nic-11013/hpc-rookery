"""Run-control facts from a MACE training log (`<name>_run-<seed>.log`):
did it finish, how it stopped, the per-epoch validation errors of every
head, the checkpoint the final model came from and the final error table.

Text in, dict out. Built on the user's parse_mace.py (per-epoch rows keyed
by (epoch, head), restarts counted by the VERIFYING SETTINGS banner, a
later session's row for the same epoch replacing the earlier one). Not
carried: the mean training loss from `*_train.txt` (tens of MB per run).
"""
import re
from collections import Counter
from datetime import datetime

_T = r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+) "
_NUM = r"([\d.eE+-]+|None|nan|inf)"
_ROW = re.compile(_T + r"INFO: (Initial|Epoch (\d+)): head: (\S+), loss=" + _NUM
                  + r", RMSE_E_per_atom=\s*" + _NUM + r" meV, RMSE_F=\s*" + _NUM
                  + r" meV / A(?:, RMSE_stress=\s*" + _NUM + r" meV / A\^3)?")
_MSG = re.compile(_T + r"(ERROR|WARNING): (.*)")


def _f(s):
    return None if s in (None, "None") else float(s)


def _ts(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f")


def parse(text: str) -> dict:
    lines = text.splitlines()
    sessions, version, heads, foundation, foundation_head = 0, None, None, None, None
    rows, initial, last_t, walls = {}, {}, {}, []
    errors, warns_seen = Counter(), Counter()
    complete = done = False
    stop, best_epoch, checkpoint, model, counts = None, None, None, None, None
    table, in_table, table_title = [], False, None
    for line in lines:
        if "VERIFYING SETTINGS" in line:
            sessions += 1
            last_t = {}
            continue
        m = _ROW.match(line)
        if m:
            t, kind, ep, head, loss, e, f, s = m.groups()
            row = {"head": head, "session": sessions, "time": t, "loss": _f(loss),
                   "rmse_e_meV_atom": _f(e), "rmse_f_meV_A": _f(f), "rmse_stress_meV_A3": _f(s)}
            if kind == "Initial":
                initial.setdefault(head, row)   # the first session: before any training
            else:
                ep, t = int(ep), _ts(t)
                if head in last_t:
                    walls.append((t - last_t[head]).total_seconds())
                last_t[head] = t
                rows[(ep, head)] = {"epoch": ep, **row}
            continue
        mm = _MSG.match(line)
        if mm:
            (errors if mm.group(2) == "ERROR" else warns_seen)[mm.group(3).strip()[:160]] += 1
            continue
        if "MACE version:" in line:
            version = line.split("MACE version:")[1].strip()
        elif "Using heads:" in line:
            heads = re.findall(r"'([^']+)'", line)
        elif "Using foundation model" in line:
            foundation = line.split("Using foundation model", 1)[1].split(" as initial")[0].strip()
        elif "as foundation head" in line:
            m2 = re.search(r"Selecting the head (\S+) as foundation head", line)
            foundation_head = m2.group(1) if m2 else None
        elif "Total number of configurations:" in line:
            m2 = re.search(r"train=(\d+), valid=(\d+)", line)
            if m2 and counts is None:
                counts = {"train": int(m2.group(1)), "valid": int(m2.group(2))}
        elif "Stopping optimization after" in line:
            m2 = re.search(r"after (\d+) epochs without improvement", line)
            stop = {"reason": "early_stop", "patience": int(m2.group(1)) if m2 else None}
        elif "Training complete" in line:
            complete = True
        elif "Loaded Stage" in line and "from epoch" in line:
            best_epoch = int(re.search(r"from epoch (\d+)", line).group(1))
        elif "Loading checkpoint:" in line:
            checkpoint = line.split("Loading checkpoint:", 1)[1].strip()
        elif "Saving model to" in line:
            model = line.split("Saving model to", 1)[1].strip()
        elif "Error-table on" in line:
            table_title, table, in_table = line.split("Error-table on", 1)[1].strip(" :"), [], True
        elif in_table and line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells and cells[0] != "config_type" and len(cells) >= 3:
                table.append({"set": cells[0], "rmse_e_meV_atom": _f(cells[1]),
                              "rmse_f_meV_A": _f(cells[2]),
                              "rel_f_rmse_pct": _f(cells[3]) if len(cells) > 3 else None,
                              "rmse_stress_meV_A3": _f(cells[4]) if len(cells) > 4 else None})
        elif in_table and not line.startswith("+"):
            in_table = False
            if re.search(r"INFO: Done\s*$", line):
                done = True
        elif re.search(r"INFO: Done\s*$", line):
            done = True

    epochs = [rows[k] for k in sorted(rows)]
    by_head = {}
    for r in epochs:
        by_head.setdefault(r["head"], []).append(r)
    last_epoch = max((r["epoch"] for r in epochs), default=None)
    if complete and stop is None:
        stop = {"reason": "max_epochs"}
    summary = {}
    for h, rs in by_head.items():
        best = next((r for r in rs if r["epoch"] == best_epoch), None)
        summary[h] = {"initial": initial.get(h), "last": rs[-1], "at_best_epoch": best,
                      "min_rmse_f": min(rs, key=lambda r: (r["rmse_f_meV_A"] is None, r["rmse_f_meV_A"]))}
    w = []
    if not sessions:
        w.append("no VERIFYING SETTINGS banner: this does not look like a MACE training log")
    if not complete:
        w.append("no 'Training complete': the run is still going, was killed or hit the time limit"
                 + (f" after epoch {last_epoch}" if last_epoch is not None else " before the first epoch")
                 + " (get_job_status; restart with mace_make_finetune restart=true)")
    elif not (done or table):
        w.append("training completed but the final evaluation did not finish (no 'Done'): the model "
                 "file may exist, the error table does not")
    for msg, n in errors.most_common(5):
        w.append(f"MACE logged ERROR x{n}: {msg}")
    for h, s in summary.items():
        i = s["initial"]
        if i and i["rmse_e_meV_atom"] is not None and i["rmse_e_meV_atom"] > 500:
            w.append(f"head {h}: initial energy RMSE {i['rmse_e_meV_atom']:.0f} meV/atom: before any "
                     "training that points at E0s on a different energy zero than the labels"
                     + (" (expected for a replay head whose labels are not the foundation model's own)"
                        if h == "pt_head" else ""))
    if sessions > 1:
        w.append(f"{sessions} sessions in this log (restarts): a later session's epochs replace earlier ones")
    return {"version": version, "sessions": sessions, "heads": heads, "foundation_model": foundation,
            "foundation_head": foundation_head, "configurations": counts,
            "finished": complete, "evaluated": done or bool(table), "stop": stop, "last_epoch": last_epoch,
            "best_epoch": best_epoch, "checkpoint": checkpoint, "model": model,
            "epoch_wall_s": round(sum(walls) / len(walls), 1) if walls else None,
            "summary": summary, "error_table": {"on": table_title, "rows": table} if table else None,
            "errors": dict(errors), "epochs": epochs, "warnings": w}
