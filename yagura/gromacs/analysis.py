"""Density statistics of a GROMACS stage from its .edr (via gmx energy; see gmx.py)."""
from .gmx import energy_series

def density_report(edr, window_ps=500, last_ps=2000, reference=None, stage_offset_ps=0.0):
    t, d = energy_series(edr, ["Temperature", "Pressure", "Volume", "Density"])
    rows, a = [], 0.0
    while a < t[-1] - 1e-6:
        m = (t >= a) & (t < a + window_ps)
        if m.any():
            rows.append({"t0_ps": a + stage_offset_ps, "t1_ps": a + window_ps + stage_offset_ps,
                         "density_g_cm3": round(float(d["Density"][m].mean()) / 1000, 4),
                         "volume_nm3": round(float(d["Volume"][m].mean()), 3),
                         "pressure_bar": round(float(d["Pressure"][m].mean()), 1),
                         "T_K": round(float(d["Temperature"][m].mean()), 1)})
        a += window_ps
    m = t >= t[-1] - last_ps
    rho = d["Density"][m] / 1000
    out = {"edr": str(edr), "length_ps": float(t[-1]), "windows": rows,
           "last_ps": min(last_ps, float(t[-1])), "mean_g_cm3": round(float(rho.mean()), 4),
           "std_g_cm3": round(float(rho.std()), 4),
           "cubic_box_nm": round(float(d["Volume"][m].mean() ** (1 / 3)), 4)}
    h = len(rho) // 2     # first vs second half of the averaging window: drift check
    out["drift_g_cm3_between_halves"] = round(float(rho[h:].mean() - rho[:h].mean()), 4)
    if reference:
        out["reference_g_cm3"] = reference
        out["percent_vs_reference"] = round(100 * (out["mean_g_cm3"] / reference - 1), 2)
    return out
