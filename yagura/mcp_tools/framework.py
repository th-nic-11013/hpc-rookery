"""yagura MCP tools: framework integrity (one frame or a trajectory).

yagura/server.py loads every module in this folder.
"""
from pathlib import Path
from yagura import framework
from yagura.mcp_tools import _local_file, mcp


@mcp.tool()
def framework_check(path: str, specorder: list[str] | None = None, index: str = ":",
                    reference_path: str | None = None, metals: list[str] | None = None,
                    metal_o_cutoff: float = 2.7, bonds: dict[str, float] | None = None,
                    overlap: float = 0.7, max_transient_frames: int = 10,
                    dt_fs: float | None = None, table_path: str | None = None,
                    overwrite: bool = False) -> dict:
    """Is the framework intact, frame by frame? For MD frames of a MOF
    (one frame, a multi-frame extended xyz, or a LAMMPS dump, plain or .gz,
    streamed so a multi-GB dump is fine). Bonds come from a reference
    (reference_path, else the first selected frame); each frame is then
    compared with it. Numpy only. Local files; fs_download a dump first,
    or a slice of it.

    Drops a frame (`drop_frames`) for: overlapping atoms (< overlap),
    a broken bond (a reference bond of a `bonds` type longer than its
    cutoff, e.g. a linker C-O), a new non-H bond (cross-linking), or a
    proton transfer (an H's nearest heavy atom changed). Reports without
    dropping: metal-O coordination (O within metal_o_cutoff) and stretched
    metal-O bonds, as excursions (runs of consecutive analysed frames
    with one bond long; longer than max_transient_frames = persistent);
    "shared H" (an H also within cutoff of a second heavy atom, a very
    short H-bond); displacement from the reference scaled affinely into
    each frame's cell. Thermal Zr-O excursions at 600 K are expected and
    belong in a training set.

    Returns {"frames_analysed", "reference", "timestep_range",
    "volume_range", "composition", "frames_dropped", "drop_frames" (first
    100 frame indices), "drop_reasons" {reason: frames}, "bonds" {type:
    {n, cutoff, min, max, frames_broken}}, "metal_o" {metals, cutoff,
    reference_bonds, frames_with_long, max_long_at_once, max_length,
    cn_histogram {CN: metal-frames}, excursions, longest_frames,
    persistent [{metal, o, frames, first_frame, last_frame}],
    persistent_count}, "displacement" {rms_max, max},
    "proton_transfer_frames", "shared_h_frames", "shared_h_first", "ok",
    "warnings", "frames" (per-frame rows, when 20 or fewer), "table"}.
    Frame indices count from 0 in the file read; for an extended xyz that
    structure_to_extxyz wrote from a dump, each row also has source and
    source_frame (the index in the dump), to join with the pool.

    Args:
        path: extended xyz (Lattice= line) or LAMMPS text dump on this machine.
        specorder: element of LAMMPS type 1, 2, ..., as in `pair_coeff * *`
            (e.g. ["H", "C", "O", "Zr"]); needed for a dump without an
            element column.
        index: frames to check: ":" (all, the default), "::10" (every 10th),
            "100:" or one frame ("-1" = last).
        reference_path: the structure the run started from (same atoms,
            same order), e.g. the relaxed cell as extended xyz; default the
            first selected frame.
        metals: elements counted as metals; default every element that is
            not H, B, C, N, O, F, Si, P, S, a halogen or a noble gas.
        metal_o_cutoff: A; a metal-O bond longer than this is "long", and CN
            counts O within it.
        bonds: framework bond types and cutoffs in A, default {"C-H": 1.35,
            "C-C": 1.85, "C-O": 1.75, "O-H": 1.35} (UiO-66 linkers and mu3-OH).
        overlap: A; any pair closer than this drops the frame.
        max_transient_frames: a metal-O bond long for more consecutive
            analysed frames than this is listed as persistent. With a
            stride, it counts analysed frames, not dump frames.
        dt_fs: MD timestep in fs (0.5 for our UiO-66 runs); adds time_ps.
        table_path: write one row per frame (TSV) here, for frame selection.
        overwrite: replace table_path if it exists.
    """
    src = _local_file(path)
    ref = str(_local_file(reference_path)) if reference_path else None
    table = None
    if table_path:
        table = Path(table_path).expanduser()
        if table.exists() and not overwrite:
            raise ValueError(f"{table} exists; pass overwrite=true to replace it")
    return framework.check(str(src), specorder, index, ref, metals, metal_o_cutoff, bonds,
                           overlap, max_transient_frames, dt_fs, str(table) if table else None)
