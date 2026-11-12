"""yagura MCP tools: labels.

yagura/server.py loads every module in this folder. A new labels tool goes here;
a new group of tools is a new module beside this one.

Comparing labelled data sets (G38): two DFT results for the same structures.
"""
from pathlib import Path
from yagura.analysis import labels as labels_mod
from yagura.mcp_tools import _local_file, mcp


@mcp.tool()
def labels_compare(a_path: str, b_path: str, pair: str = "geometry", tol: float = 0.001,
                   energy_key: str | None = None, forces_key: str | None = None,
                   moment_key: str | None = None, force_rms_mev: float = 50.0,
                   energy_mev: float = 5.0, out_path: str | None = None,
                   overwrite: bool = False) -> dict:
    """Compare two labelled extxyz sets of the same structures frame by
    frame, to find labels that sit in another state (spin state, SCF
    solution) in one of them: a relabel at other settings, another starting
    MAGMOM, +U, or another code. Same metrics as the MXene campaign's
    compare_magmom_test.py. Local only; numpy only.

    Frames are paired by geometry (same atoms in the same order, positions
    within tol), so b may be a subset or a reordering of a. For each pair:
    dE per atom (meV) and its distance from the median shift (a change of
    method shifts every frame alike; a frame far from the median is the
    odd one), the per-atom force difference |F_b - F_a| as RMS and max
    (meV/A, with the atom), and, when both sets carry per-atom moments
    (REF_magmoms / magmoms), the sites whose moment changed sign and the
    "opposed" sites per element in each. A pair is flagged when its force
    RMS exceeds force_rms_mev, its dE is more than energy_mev from the
    median, or a site moment changed sign.

    Before it: the two label files (cp2k_make_labels, or the campaign's
    OUTCAR converter), fs_download'ed. After it: drop or relabel the
    flagged frames before training (hyogo's mlip_check_dataset checks the
    set as a whole).

    Returns {"out" (the TSV, one row per pair), "keys" (the energy, forces
    and moment keys used per file), "pairs", "frames", "median_shift_mev_atom",
    "de_mev_atom", "de_vs_median", "df_rms_mev_a", "df_max_mev_a" (each
    {mean, rms, min, max}), "thresholds", "flagged_count", "flagged" (the
    worst 20 pairs: a and b frame indices, config_type, dE, dE vs median,
    force RMS and max with the atom, total moments, flipped_sites, opposed
    {element: [a, b]}, reasons), "warnings"}.

    Args:
        a_path: the reference labels (extxyz) on this machine.
        b_path: the labels to compare with them.
        pair: "geometry" (default) or "index" (frame i with frame i; refused
            when their atoms or positions differ).
        tol: largest position difference (A) for two frames to be the same
            structure.
        energy_key, forces_key, moment_key: the label keys, the same in both
            files; default the first present in every frame (REF_energy,
            energy, ...; REF_forces, forces, ...; REF_magmoms, magmoms, ...).
        force_rms_mev: force-difference RMS (meV/A) above which a pair is flagged.
        energy_mev: |dE per atom - median shift| (meV) above which a pair is flagged.
        out_path: the TSV; default <b stem>_vs_<a stem>.tsv beside b.
        overwrite: replace out_path if it exists.
    """
    a, b = _local_file(a_path), _local_file(b_path)
    if a.resolve() == b.resolve():
        raise ValueError("a_path and b_path are the same file")
    dst = Path(out_path).expanduser() if out_path else b.with_name(f"{b.stem}_vs_{a.stem}.tsv")
    if dst.resolve() in (a.resolve(), b.resolve()):
        raise ValueError("out_path would overwrite a label file")
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} exists; pass overwrite=true to replace it")
    result, tsv = labels_mod.compare(a.read_text(errors="replace"), b.read_text(errors="replace"),
                                     a.name, b.name, pair, tol, energy_key, forces_key, moment_key,
                                     force_rms_mev, energy_mev)
    dst.write_text(tsv)
    return {"out": str(dst), **result}
