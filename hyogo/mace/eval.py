"""Score an MLIP against labelled frames (G47): the labels (an extxyz, e.g.
cp2k_make_labels' out) and one predictions file per model (written on the
cluster by the mace pack's eval script, codes/mace/eval/scripts/predict.py)
-> energy, force and stress errors per config_type and overall, and the
worst frames.

Text and dicts in, dicts out: no file I/O, no ASE, no torch.

Energies are compared two ways:
- relative: each frame's energy per atom minus the reference frame's, model
  against labels (what a compression curve or a phase ordering needs; the
  energy zero of each side cancels). This is T1's dE vs f0 (job 9751103).
- absolute after E0s: (E - sum of E0s) / atoms on each side, the labels'
  E0s (cp2k_make_e0s) against the model's own (recorded by predict.py). A
  foundation model and CP2K labels have different energy zeros, so the
  plain absolute difference means nothing; this one does.

Forces: RMSE and MAE over all components (T1's force RMSE) and the mean
per-atom cosine (atoms where either force is under 1 meV/A are skipped).
Stress: RMSE over the 6 Voigt components, ASE sign, in meV/A^3 and GPa.

Refusals: frames that are not the same (count, source_sha256, symbols and
positions in order), labels mixing D3 and D3-free frames, and a model whose
dispersion differs from the labels'. The one way past the last is
d3_mismatch="energy_only" for D3 labels and a D3-free model: label energies
lose their dispersion_eV, while forces and stress still contain D3 (CP2K
prints no D3 forces), so their errors are upper bounds. That is how T1 was
scored.
"""
import hashlib
import math

import numpy as np

from hyogo.mace import dataset

FORMAT = "mlip-predictions/1"
COS_MIN_EV_A = 1e-3       # atoms with a smaller force on either side don't enter the cosine
GPA_PER_EV_A3 = 160.21766208
VOIGT = ((0, 0), (1, 1), (2, 2), (1, 2), (0, 2), (0, 1))


def atoms_sha256(symbols, positions) -> str:
    """The same string codes/mace/eval/scripts/predict.py hashes: keep in step."""
    text = "".join(f"{s} {x:.5f} {y:.5f} {z:.5f}\n" for s, (x, y, z) in zip(symbols, positions))
    return hashlib.sha256(text.encode()).hexdigest()


def _column(frame: dict, name: str) -> np.ndarray | None:
    """A per-atom column of an extxyz frame as floats (n, count), or None."""
    cols = dataset._columns(frame["info"]["Properties"]) if "Properties" in frame["info"] else \
        [("species", "S", 1), ("pos", "R", 3)]
    off = 0
    for cname, _, count in cols:
        if cname == name:
            rows = frame["text"].splitlines()[2:2 + frame["natoms"]]
            return np.array([[float(v) for v in r.split()[off:off + count]] for r in rows])
        off += count
    return None


def _stress(value: str) -> np.ndarray:
    v = np.array([float(x) for x in value.split()])
    if v.size == 9:
        m = v.reshape(3, 3)
        v = np.array([m[i, j] for i, j in VOIGT])
    if v.size != 6:
        raise ValueError(f"stress {value[:60]!r} has {v.size} values; expected 6 (Voigt) or 9")
    return v


def read_labels(frames: list[dict], energy_key=None, forces_key=None, stress_key=None) -> dict:
    """Numbers and identity of every labelled frame."""
    keys = dataset.pick_keys(frames, energy_key, forces_key, stress_key)
    if keys["energy_key"] is None or keys["forces_key"] is None:
        raise ValueError(f"the labels lack an energy or forces key in every frame ({keys}); "
                         "pass energy_key / forces_key")
    d3 = sum("dispersion_eV" in f["info"] for f in frames)
    if 0 < d3 < len(frames):
        raise ValueError(f"{d3} of {len(frames)} labelled frames carry dispersion_eV (D3) and the rest "
                         "do not: D3 and D3-free labels are mixed (mlip_check_dataset)")
    out = []
    for i, f in enumerate(frames):
        pos = _column(f, "pos")
        forces = _column(f, keys["forces_key"])
        if pos is None or forces is None or forces.shape != (f["natoms"], 3):
            raise ValueError(f"{f['source']} frame {f['index']}: no positions or "
                             f"{keys['forces_key']} (n x 3) column")
        st = f["info"].get(keys["stress_key"]) if keys["stress_key"] else None
        out.append({"index": i, "natoms": f["natoms"], "symbols": f["symbols"],
                    "config_type": f["info"].get("config_type", ""),
                    "source_sha256": f["info"].get("source_sha256"),
                    "source": f["info"].get("source"),
                    "atoms_sha256": atoms_sha256(f["symbols"], pos),
                    "energy": float(f["info"][keys["energy_key"]]),
                    "dispersion": float(f["info"]["dispersion_eV"]) if "dispersion_eV" in f["info"] else None,
                    "forces": forces, "stress": None if st is None else _stress(st)})
    return {"frames": out, "keys": keys,
            "d3": "included" if d3 == len(frames) else "none"}


def _check_predictions(pred: dict, labels: list[dict], name: str) -> None:
    if pred.get("format") != FORMAT:
        raise ValueError(f"{name}: not a predictions file of format {FORMAT} (got "
                         f"{pred.get('format')!r}); write it with the mace pack's eval/scripts/predict.py")
    pf = pred["frames"]
    if len(pf) != len(labels):
        raise ValueError(f"{name}: {len(pf)} predicted frames for {len(labels)} labelled ones: "
                         "predictions of another labels file")
    bad = []
    for p, l in zip(pf, labels):
        why = []
        if p["natoms"] != l["natoms"]:
            why.append(f"{p['natoms']} vs {l['natoms']} atoms")
        if p.get("source_sha256") != l["source_sha256"]:
            why.append("source_sha256 differs")
        if p["atoms_sha256"] != l["atoms_sha256"]:
            why.append("symbols or positions differ (atom order)")
        if why:
            bad.append(f"frame {l['index']} ({l['config_type'] or 'no config_type'}): {', '.join(why)}")
    if bad:
        raise ValueError(f"{name}: {len(bad)} frame(s) are not the labelled ones: " + "; ".join(bad[:5])
                         + ". Re-run predict.py on this labels file")


def _e0_sum(symbols, e0s: dict) -> float:
    return sum(e0s[str(dataset.Z_OF[s])] for s in symbols)


def norm_e0s(e0s: dict, what: str) -> dict:
    """{str(Z): eV} from keys that are atomic numbers or element symbols."""
    out = {}
    for k, v in e0s.items():
        z = str(k) if str(k).isdigit() else str(dataset.Z_OF.get(str(k), ""))
        if not z:
            raise ValueError(f"{what}: key {k!r} is neither an atomic number nor an element symbol")
        out[z] = float(v)
    return out


def _rms(x) -> float | None:
    x = np.asarray(x, dtype=float)
    return float(math.sqrt(np.mean(x ** 2))) if x.size else None


def _group(rows: list[dict], fl: list, fp: list, cos: list, ref_index: int) -> dict:
    rel = [r["dE_error_meV_atom"] for r in rows if r["index"] != ref_index]
    ab = [r["E_abs_error_meV_atom"] for r in rows if r["E_abs_error_meV_atom"] is not None]
    st = [r["_stress_diff"] for r in rows if r["_stress_diff"] is not None]
    dF = np.concatenate([(p - l).ravel() for p, l in zip(fp, fl)]) * 1e3
    c = np.concatenate(cos) if cos else np.array([])
    out = {"frames": len(rows), "atoms": int(sum(r["natoms"] for r in rows)),
           "energy_rel_meV_atom": None if not rel else {
               "mae": float(np.mean(np.abs(rel))), "rmse": _rms(rel),
               "max_abs": float(np.max(np.abs(rel))), "mean": float(np.mean(rel))},
           "energy_abs_meV_atom": None if not ab else {
               "mean": float(np.mean(ab)), "mae": float(np.mean(np.abs(ab))), "rmse": _rms(ab),
               "std": float(np.std(ab))},
           "forces": {"rmse_meV_A": _rms(dF), "mae_meV_A": float(np.mean(np.abs(dF))),
                      "cosine_mean": float(np.mean(c)) if c.size else None,
                      "cosine_atoms": int(c.size)},
           "stress": None if not st else {
               "frames": len(st), "rmse_meV_A3": _rms(np.concatenate(st)) * 1e3,
               "rmse_GPa": _rms(np.concatenate(st)) * GPA_PER_EV_A3}}
    return out


def _cosines(p: np.ndarray, l: np.ndarray) -> np.ndarray:
    np_, nl = np.linalg.norm(p, axis=1), np.linalg.norm(l, axis=1)
    keep = (np_ >= COS_MIN_EV_A) & (nl >= COS_MIN_EV_A)
    return np.sum(p[keep] * l[keep], axis=1) / (np_[keep] * nl[keep])


def score(labels: dict, pred: dict, name: str, ref: int, e0s: dict | None, d3_mismatch: str,
          worst: int, per_frame: bool) -> dict:
    """One model's errors against the labels (read_labels' dict)."""
    lf = labels["frames"]
    _check_predictions(pred, lf, name)
    warnings = list(pred.get("warnings") or [])
    model_d3 = pred.get("dispersion")
    if model_d3 not in ("none", "included"):
        raise ValueError(f"{name}: dispersion {model_d3!r} is not stated as 'none' or 'included'")
    sub_disp = False
    if model_d3 != labels["d3"]:
        if labels["d3"] == "included" and model_d3 == "none" and d3_mismatch == "energy_only":
            sub_disp = True
            warnings.append(f"{name}: D3-free model against labels with D3: label energies lose their "
                            "dispersion_eV; forces and stress still contain D3 (CP2K prints no D3 "
                            "forces), so their errors are upper bounds")
        else:
            raise ValueError(
                f"{name}: the model's energy has dispersion {model_d3!r} and the labels' has "
                f"{labels['d3']!r} (dispersion_eV on the frames): the errors would mix in D3. Label "
                "D3-free frames (cp2k_make_input d3=\"none\")"
                + ("; or pass d3_mismatch=\"energy_only\" to compare energies without D3 and forces "
                   "as upper bounds" if labels["d3"] == "included" else ""))
    m_e0 = (pred.get("model") or {}).get("e0s")
    if e0s is not None and m_e0 is None:
        warnings.append(f"{name}: the predictions carry no model E0s; no absolute energy error")
    if e0s is not None and m_e0 is not None:
        m_e0 = norm_e0s(m_e0, f"{name} model E0s")
        need = {str(dataset.Z_OF[s]) for f in lf for s in f["symbols"]}
        for what, table in (("labels' e0s", e0s), (f"{name} model E0s", m_e0)):
            lack = sorted(need - set(table), key=int)
            if lack:
                raise ValueError(f"{what} lack atomic numbers {lack}")

    e_lab = [f["energy"] - (f["dispersion"] if sub_disp else 0.0) for f in lf]
    e_mod = [p["energy"] for p in pred["frames"]]
    epa_l = [e / f["natoms"] for e, f in zip(e_lab, lf)]
    epa_m = [e / f["natoms"] for e, f in zip(e_mod, lf)]
    rows, fl, fp, cos = [], [], [], []
    for i, (f, p) in enumerate(zip(lf, pred["frames"])):
        F = np.asarray(p["forces"], dtype=float)
        dF = (F - f["forces"]) * 1e3
        c = _cosines(F, f["forces"])
        sd = None
        if f["stress"] is not None and p.get("stress") is not None:
            sd = np.asarray(p["stress"], dtype=float) - f["stress"]
        de_l = (epa_l[i] - epa_l[ref]) * 1e3
        de_m = (epa_m[i] - epa_m[ref]) * 1e3
        eabs = None
        if e0s is not None and m_e0 is not None:
            eabs = ((e_mod[i] - _e0_sum(f["symbols"], m_e0))
                    - (e_lab[i] - _e0_sum(f["symbols"], e0s))) / f["natoms"] * 1e3
        rows.append({"index": f["index"], "config_type": f["config_type"], "natoms": f["natoms"],
                     "source": f["source"],
                     "dE_label_meV_atom": de_l, "dE_model_meV_atom": de_m, "dE_error_meV_atom": de_m - de_l,
                     "E_abs_error_meV_atom": eabs,
                     "force_rmse_meV_A": _rms(dF), "force_cosine": float(np.mean(c)) if c.size else None,
                     "stress_rmse_meV_A3": None if sd is None else _rms(sd) * 1e3,
                     "_stress_diff": sd})
        fl.append(f["forces"])
        fp.append(F)
        cos.append(c)
    groups = {}
    for i, r in enumerate(rows):
        groups.setdefault(r["config_type"], []).append(i)
    by_type = {ct: _group([rows[i] for i in idx], [fl[i] for i in idx], [fp[i] for i in idx],
                          [cos[i] for i in idx], lf[ref]["index"])
               for ct, idx in groups.items()} if len(groups) > 1 or "" not in groups else {}
    overall = _group(rows, fl, fp, cos, lf[ref]["index"])
    for r in rows:
        r.pop("_stress_diff")
    short = lambda r, k: {"index": r["index"], "config_type": r["config_type"], k: r[k]}
    worst_f = sorted(rows, key=lambda r: -r["force_rmse_meV_A"])[:worst]
    worst_e = sorted((r for r in rows if r["index"] != lf[ref]["index"]),
                     key=lambda r: -abs(r["dE_error_meV_atom"]))[:worst]
    out = {"model": pred.get("model"), "dispersion": model_d3,
           "label_energy": "REF_energy - dispersion_eV" if sub_disp else labels["keys"]["energy_key"],
           "overall": overall, "by_config_type": by_type,
           "worst": {"force_rmse": [short(r, "force_rmse_meV_A") for r in worst_f],
                     "energy_rel": [short(r, "dE_error_meV_atom") for r in worst_e]},
           "warnings": warnings}
    if per_frame:
        out["frames"] = rows
    return out


def compare(label_frames: list[dict], predictions: list[dict], reference_frame=0,
            e0s: dict | None = None, d3_mismatch: str = "refuse", worst: int = 5,
            per_frame: bool = False, energy_key=None, forces_key=None, stress_key=None,
            names: list[str] | None = None) -> dict:
    """Every model's errors against the same labels."""
    if d3_mismatch not in ("refuse", "energy_only"):
        raise ValueError('d3_mismatch must be "refuse" or "energy_only"')
    if not predictions:
        raise ValueError("no predictions: pass one predictions file per model (predict.py's out)")
    labels = read_labels(label_frames, energy_key, forces_key, stress_key)
    lf = labels["frames"]
    if isinstance(reference_frame, bool) or not isinstance(reference_frame, (int, str)):
        raise ValueError("reference_frame is a frame index or a config_type naming one frame")
    if isinstance(reference_frame, str):
        hit = [i for i, f in enumerate(lf) if f["config_type"] == reference_frame]
        if len(hit) != 1:
            raise ValueError(f"reference_frame {reference_frame!r} names {len(hit)} frames by "
                             "config_type; give a frame index")
        ref = hit[0]
    else:
        if not 0 <= reference_frame < len(lf):
            raise ValueError(f"reference_frame {reference_frame} is not a frame index (0 to {len(lf) - 1})")
        ref = reference_frame
    if e0s is not None:
        e0s = norm_e0s(e0s, "e0s")
    warnings = []
    if labels["d3"] == "none" and not any(f["source_sha256"] for f in lf):
        warnings.append("the labels carry no source_sha256 or dispersion_eV (not from "
                        "cp2k_make_labels): they are taken as D3-free")
    models = {}
    for k, pred in enumerate(predictions):
        name = (names[k] if names else None) or (pred.get("model") or {}).get("name") or f"model{k}"
        if name in models:
            name = f"{name}#{k}"
        models[name] = score(labels, pred, name, ref, e0s, d3_mismatch, worst, per_frame)
    shas = {(p.get("labels") or {}).get("sha256") for p in predictions}
    return {"labels": {"frames": len(lf), "d3": labels["d3"], "keys": labels["keys"],
                       "config_types": sorted({f["config_type"] for f in lf})},
            "reference_frame": {"index": lf[ref]["index"], "config_type": lf[ref]["config_type"]},
            "models": models, "warnings": warnings, "_prediction_label_shas": shas}
