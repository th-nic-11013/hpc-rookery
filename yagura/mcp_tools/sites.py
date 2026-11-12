"""yagura MCP tools: sites.

yagura/server.py loads every module in this folder. A new sites tool goes here;
a new group of tools is a new module beside this one.

Per-site physics by environment (G37): where a moment or a charge sits.
"""
from pathlib import Path
from yagura.analysis import sites as sites_mod
from yagura.mcp_tools import _local_file, mcp


@mcp.tool()
def sites_by_environment(path: str, frame: int = -1, metals: list[str] | None = None,
                         h_cut: float = 1.25, water_min: float = 3.5, slab_max: float = 2.5,
                         rank_by: str | None = None, top: int = 5, out_path: str | None = None,
                         overwrite: bool = False) -> dict:
    """Class each atom of a structure by its environment and summarise its
    per-site values (moments, charges, any one-column array) by class: is a
    stray moment on the metal, on water O or on slab O; does charge leak out
    of the water. The MXene campaign's stray_moment.py, water_charge.py and
    label_spins.py in one call. Local only; numpy only.

    Classes: "metal"; O as "water_O" (2 H within h_cut and more than
    water_min from any metal), "slab_O" (closer than slab_max to a metal)
    or "other_O" (OH, H3O+, O near the slab); every other atom by its
    element. Distances use the nearest periodic image.

    Before it: hyogo's vasp_parse_output with sites_out (needs LORBIT >= 10)
    writes such a file from an OUTCAR; any extxyz with a Lattice and
    per-site columns works.

    Returns {"out" (TSV: one row per atom with class, nH, dM and every
    column), "natoms", "metals", "cell_lengths", "info" (the frame's
    comment-line values: e_fermi, magnetization, REF_energy, ...),
    "columns", "classes": {class: {count, column: {mean, sum, sum_abs, min,
    max}}}, "largest" (the top sites by |rank_by|, default the moments,
    with nH, dM and class), "metal_spin" ({all, element: {n, net,
    antiparallel (< -0.5 uB), mean_positive (of sites > 0.5 uB)}}),
    "thresholds", "warnings"}.

    Args:
        path: the extxyz on this machine.
        frame: which frame (default the last).
        metals: the metal elements; default every element that isn't a
            non-metal (H, C, N, O, halogens, noble gases, B, Si, P, S, ...).
        h_cut: H within this (A) counts toward nH.
        water_min: an O with 2 H is water_O beyond this distance (A) from any metal.
        slab_max: an O closer than this (A) to a metal is slab_O.
        rank_by: the column "largest" ranks by; default the moments.
        top: how many sites "largest" lists.
        out_path: the TSV; default <stem>.environments.tsv beside the file.
        overwrite: replace out_path if it exists.
    """
    src = _local_file(path)
    dst = Path(out_path).expanduser() if out_path else src.with_name(f"{src.stem}.environments.tsv")
    if dst.resolve() == src.resolve():
        raise ValueError("out_path would overwrite the structure")
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} exists; pass overwrite=true to replace it")
    if not 0 < h_cut < water_min or slab_max <= 0:
        raise ValueError("thresholds: 0 < h_cut < water_min, and slab_max > 0")
    result, tsv = sites_mod.summarise(src.read_text(errors="replace"), src.name, frame, metals,
                                      h_cut, water_min, slab_max, rank_by, top)
    dst.write_text(tsv)
    return {"out": str(dst), **result}
