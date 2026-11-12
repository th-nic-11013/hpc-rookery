"""yagura MCP tools: structure.

yagura/server.py loads every module in this folder. A new structure tool goes here;
a new group of tools is a new module beside this one.
"""
from pathlib import Path
from yagura import geomcheck, lammps_dump, stretch
from yagura.mcp_tools import _ase, _local_file, mcp


@mcp.tool()
def structure_check(path: str) -> dict:
    """Sanity-check a periodic structure (extended xyz with a Lattice=
    line) before it becomes an input: composition, cell, the shortest
    distance per element pair (minimum image, pairs below 3.2 A), and
    flags. Local file only; for a file on a cluster, fs_download it first.

    Flags: "overlap" (any pair below 0.7 A: duplicated atoms, typically
    disorder left from a CIF) and "short_xh" (C-H or N-H below 1.0 A:
    X-ray hydrogen positions). `ok` is true when nothing is flagged.

    Returns {"natoms", "composition", "cell": {lengths, angles, volume,
    widths}, "min_pair_distances": [{pair, d, count}], "flags":
    [{pair, d, kind}], "ok"}.

    Args:
        path: an extended xyz file on this machine; ~ is expanded.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"{p} is not a file on this machine. For a file on a "
                         "cluster, download it first (fs_download).")
    return geomcheck.check(p.read_text())


@mcp.tool()
def structure_stretch_xh(path: str, out_path: str, length: float = 1.08,
                         pairs: list[str] | None = None, overwrite: bool = False) -> dict:
    """Set X-H bond lengths in an extended xyz: every H within 1.2 A of an
    atom X (minimum image) is moved along that bond to `length`. Use it
    when structure_check flags `short_xh` (X-ray H positions, C-H near
    0.95 A). Local files only; writes a new file, keeping line 2, the
    other atoms and any extra columns.

    Returns {"out", "moved", "pairs", "length", "shortest_before"}, then
    run structure_check on `out`.

    Args:
        path: the extended xyz to read, on this machine.
        out_path: where to write the result; must differ from path.
        length: target X-H length in A (1.08 for C-H; ~1.01 N-H, ~0.97 O-H).
        pairs: X-H pairs to fix, e.g. ["C-H", "N-H"]; default ["C-H"].
        overwrite: replace out_path if it exists.
    """
    src, dst = Path(path).expanduser(), Path(out_path).expanduser()
    if not src.is_file():
        raise ValueError(f"{src} is not a file on this machine. For a file on a "
                         "cluster, download it first (fs_download).")
    if dst.resolve() == src.resolve():
        raise ValueError("out_path must differ from path: the original is kept")
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} exists; pass overwrite=true to replace it")
    text, info = stretch.stretch(src.read_text(), length, tuple(pairs or ["C-H"]))
    dst.write_text(text)
    return {"out": str(dst), **info}


@mcp.tool()
def cif_inspect(path: str) -> dict:
    """Summarise a CIF before building from it: cell, space group keys,
    and every atom site with its occupancy and disorder group. Partial
    sites are the ones to resolve first: within one disorder group,
    occupancies summing to 1 are alternative positions; a common value
    below 1 means vacancies. Reads only, never repairs. Needs ASE.

    Returns {"cell": {lengths, angles}, "keys": {CIF key: value} (space
    group name and number, formula, Z, when present), "sites": [{label,
    element, x, y, z, occupancy, group}], "partial": [labels],
    "partial_groups": {group: [labels]}}.

    Args:
        path: a CIF on this machine.
    """
    return _ase("inspect_cif").inspect(str(_local_file(path)))


@mcp.tool()
def structure_to_extxyz(source_path: str, out_path: str, vacuum: float | None = None,
                        charge: int = 0, index: str = "-1", overwrite: bool = False,
                        specorder: list[str] | None = None, dt_fs: float | None = None,
                        time_range_ps: list[float | None] | None = None,
                        source: str | None = None, config_type: str | None = None) -> dict:
    """Convert any structure ASE reads (CIF, POSCAR, PDB, xyz, ...) into
    extended xyz with the cell on line 2, the format the other structure
    tools read. Converts, never repairs: partial CIF sites come out as
    full atoms (resolve them first, see cif_inspect), and a structure
    without a full cell is refused unless `vacuum` boxes it. Needs ASE.
    Run structure_check on the result.

    A LAMMPS text dump (plain or .gz; recognised by its first line) is
    read without ASE and streamed, so a multi-GB dump is fine: types
    become elements through `specorder`, triclinic boxes are read, and
    `index` picks one frame or a range/stride ("::50"). Each frame's
    comment line carries timestep, time_ps (with dt_fs), volume, source
    (run label), frame (index in the dump), source_path and config_type,
    the keys hyogo's mlip_select_frames reads. Then framework_check them.

    Returns {"out", "natoms", "formula", "cell": {lengths, angles},
    "electrons" (all-electron, at `charge`), "charge", "multiplicities"
    (the allowed spin multiplicities), "warnings"}; for a dump instead
    {"out", "frames_written", "natoms", "formula", "composition", "source",
    "first", "last" (each
    {frame, timestep, volume, time_ps}), "volume_range", "electrons",
    "charge", "multiplicities", "warnings"}.

    Args:
        source_path: the structure file on this machine.
        out_path: the extended xyz to write; must differ from source_path.
        vacuum: centre an isolated molecule in a box with this much vacuum
            (A) on every side.
        charge: net charge, for the electron count.
        index: frame of a multi-frame file, in ASE's index syntax ("-1" = last);
            for a dump also a range START:STOP:STEP ("::50", "100:", ":" = all).
        overwrite: replace out_path if it exists.
        specorder: for a LAMMPS dump, the element of type 1, 2, ..., as in
            `pair_coeff * *` (e.g. ["H", "C", "O", "Zr"]).
        dt_fs: for a dump, the MD timestep in fs (0.5 in our UiO-66 runs);
            adds time_ps to each frame.
        time_range_ps: for a dump, keep frames with time_ps in [start, end]
            (either may be null), e.g. [10, null] drops equilibration; needs dt_fs.
        source: for a dump, the run label written to every frame (no spaces),
            e.g. "A3-300K"; give every chunk of one run the same label.
            Default: the dump's file stem.
        config_type: for a dump, a stratum label for every frame, e.g. "P1-300K".
    """
    src, dst = _local_file(source_path), Path(out_path).expanduser()
    if dst.resolve() == src.resolve():
        raise ValueError("out_path must differ from source_path: the original is kept")
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} exists; pass overwrite=true to replace it")
    if lammps_dump.is_dump(src):
        if vacuum is not None:
            raise ValueError("vacuum is for an isolated molecule; a LAMMPS dump has its box")
        r = lammps_dump.write_extxyz(str(src), str(dst), specorder, index, dt_fs, time_range_ps,
                                     source, config_type)
        electrons = sum(lammps_dump.Z[e] * c for e, c in r["composition"].items()) - charge
        return {**r, "electrons": electrons, "charge": charge,
                "multiplicities": "1, 3, 5, ..." if electrons % 2 == 0 else "2, 4, 6, ...",
                "warnings": []}
    if specorder or dt_fs is not None or time_range_ps is not None or source or config_type:
        raise ValueError("specorder, dt_fs, time_range_ps, source and config_type are for a LAMMPS dump; "
                         f"{src.name} does not start with 'ITEM: TIMESTEP'")
    warnings = []
    if src.suffix.lower() == ".cif":
        partial = _ase("inspect_cif").inspect(str(src))["partial"]
        if partial:
            warnings.append(f"the CIF has partial sites ({', '.join(partial)}); each came out as a "
                            "full atom. Resolve the disorder first (cif_inspect, "
                            "references/crystal-to-cell.md)")
    r = _ase("to_extxyz").convert(str(src), str(dst), vacuum, charge, index)
    return {**r, "warnings": warnings}


@mcp.tool()
def structure_to_lammps_data(source_path: str, out_path: str | None = None,
                             specorder: list[str] | None = None, index: str = "-1",
                             overwrite: bool = False) -> dict:
    """Write a structure as a LAMMPS data file for a machine-learned
    potential (atom_style atomic, metal units, masses), one type per
    element, in order of atomic number unless specorder says otherwise.
    Also writes, beside it, <stem>.types.in (a comment naming the types and
    the `pair_coeff * * Z...` line that pair_style metatomic reads) and
    <stem>.lammps.xyz (the atoms read back from the data file: a triclinic
    cell is rotated to LAMMPS' lower-triangular form, so ASE references
    must use this file, not the source). Same as HOKUSAI's MLMD benchmark
    prep_structures.py. Local only; fs_upload the .data and .types.in to the
    run folder. Needs ASE.

    Before it: structure_check the structure. After it: hyogo's
    lammps_make_input with this result's `elements`.

    Returns {"data", "types_in", "xyz", "natoms", "formula", "elements"
    (type 1, 2, ...), "atomic_numbers", "masses", "pair_coeff", "cell":
    {lengths, angles, triclinic}, "rotated", "warnings"}.

    Args:
        source_path: any structure ASE reads (extended xyz, CIF, POSCAR, ...)
            with a full cell, on this machine.
        out_path: the .data file to write; default <stem>.data beside the source.
        specorder: element for type 1, 2, ...; give it to share one input
            between systems (e.g. ["H", "C", "O", "Zr"] for UiO-66 with and
            without guests).
        index: frame of a multi-frame file ("-1" = last).
        overwrite: replace the three files if they exist.
    """
    src = _local_file(source_path)
    data = Path(out_path).expanduser() if out_path else src.with_suffix(".data")
    types = data.with_suffix(".types.in")
    xyz = data.with_suffix(".lammps.xyz")
    if data.suffix != ".data":
        raise ValueError(f"out_path ends in .data (got {data.name})")
    if src.resolve() in (data.resolve(), xyz.resolve()):
        raise ValueError("out_path would overwrite the source; choose another name")
    for f in (data, types, xyz):
        if f.exists() and not overwrite:
            raise ValueError(f"{f} exists; pass overwrite=true to replace it")
    return _ase("to_lammps_data").convert(str(src), str(data), str(types), str(xyz),
                                         specorder, index)
