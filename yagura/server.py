"""Entry point for the yagura MCP server (yagura-mcp).

Tools take paths on this machine, never a facility: yagura does not reach
a cluster. For a file on a cluster, fetch it first (rookery's fs_download).

The CIF tools need ASE, the optional [yagura] extra. It is imported inside
them, so the server and the numpy-only tools (the checks and the
trajectory analysis) work without it.
"""
import argparse
import importlib
from pathlib import Path

from yagura import geomcheck, stretch
from yagura.analysis import fes as fes_mod, trajectory
from yagura.mcp_server import MCPServer

mcp = MCPServer("yagura-mcp")


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


def _ase(module: str):
    """Import a yagura module that needs ASE, or say how to get it."""
    try:
        return importlib.import_module(f"yagura.{module}")
    except ModuleNotFoundError as e:
        if e.name and e.name.split(".")[0] == "ase":
            raise RuntimeError("this tool needs ASE, which is not installed: install the "
                               "optional extra (pip install 'hpc-rookery[yagura]', or "
                               "pip install ase) and restart the yagura server") from e
        raise


def _local_file(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"{p} is not a file on this machine. For a file on a "
                         "cluster, download it first (fs_download).")
    return p


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
                        charge: int = 0, index: str = "-1", overwrite: bool = False) -> dict:
    """Convert any structure ASE reads (CIF, POSCAR, PDB, xyz, ...) into
    extended xyz with the cell on line 2, the format the other structure
    tools read. Converts, never repairs: partial CIF sites come out as
    full atoms (resolve them first, see cif_inspect), and a structure
    without a full cell is refused unless `vacuum` boxes it. Needs ASE.
    Run structure_check on the result.

    Returns {"out", "natoms", "formula", "cell": {lengths, angles},
    "electrons" (all-electron, at `charge`), "charge", "multiplicities"
    (the allowed spin multiplicities), "warnings"}.

    Args:
        source_path: the structure file on this machine.
        out_path: the extended xyz to write; must differ from source_path.
        vacuum: centre an isolated molecule in a box with this much vacuum
            (A) on every side.
        charge: net charge, for the electron count.
        index: frame of a multi-frame file, in ASE's index syntax ("-1" = last).
        overwrite: replace out_path if it exists.
    """
    src, dst = _local_file(source_path), Path(out_path).expanduser()
    if dst.resolve() == src.resolve():
        raise ValueError("out_path must differ from source_path: the original is kept")
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} exists; pass overwrite=true to replace it")
    warnings = []
    if src.suffix.lower() == ".cif":
        partial = _ase("inspect_cif").inspect(str(src))["partial"]
        if partial:
            warnings.append(f"the CIF has partial sites ({', '.join(partial)}); each came out as a "
                            "full atom. Resolve the disorder first (cif_inspect, "
                            "references/crystal-to-cell.md)")
    r = _ase("to_extxyz").convert(str(src), str(dst), vacuum, charge, index)
    return {**r, "warnings": warnings}


Cell = list[float] | list[list[float]] | None


@mcp.tool()
def trajectory_rdf(path: str, pairs: list[str], r_max: float | None = None, bins: int = 120,
                   cell: Cell = None, frames: str = ":") -> dict:
    """Radial distribution function g(r) per element pair, minimum image,
    averaged over the frames of an MD trajectory: multi-frame extended xyz
    (a Lattice= line per frame) or CP2K's PROJECT-pos-1.xyz, which has no
    cell, so pass `cell`. Local file only; numpy only.

    Units: r in A; g dimensionless (1 = uncorrelated). Returns {"r" (bin
    centres), "pairs": [{pair, n_a, n_b, g, "peaks": [{r, g}] (up to 3
    maxima of the 3-bin-smoothed g above 1), "first_min": {r, g,
    coordination} (the first minimum after the first peak; coordination =
    mean number of B within r of an A, or null)}],
    "r_max", "frames_used", "cell_source", "warnings"}.

    Args:
        path: the trajectory on this machine.
        pairs: element pairs, e.g. ["O-H", "O-O"].
        r_max: largest r in A; at most half the smallest perpendicular
            cell width (refused beyond it), which is the default.
        bins: histogram bins over 0..r_max (at most 1000).
        cell: 3 lengths or a 3x3 matrix of cell vectors (rows), in A;
            required when the file has no Lattice= line, and overrides it.
        frames: START:STOP:STEP over the file's frames, e.g. "1000::10".
    """
    return trajectory.rdf(_local_file(path), pairs, r_max, bins, cell, frames)


@mcp.tool()
def trajectory_msd(path: str, elements: list[str], dt_fs: float, cell: Cell = None,
                   frames: str = ":", fit_ps: list[float] | None = None,
                   points: int = 100, remove_drift: bool = True) -> dict:
    """Mean-squared displacement per element from an MD trajectory, and
    the diffusion coefficient D = slope/6 of a straight line over a fit
    window. Positions are unwrapped (each step between frames taken as
    the minimum image) and every time origin is used; lags go up to half
    the trajectory. Same input as trajectory_rdf; numpy only.

    Units: t in ps, MSD in A^2, D in A^2/ps (1 A^2/ps = 1e-4 cm^2/s; also
    given as D_cm2_per_s). Returns {"t_ps" (at most `points` lags),
    "elements": [{element, natoms, msd, D_A2_per_ps, D_cm2_per_s, "fit":
    {slope_A2_per_ps, intercept_A2, r2}, loglog_slope}], "fit_window_ps",
    "method", "drift", "warnings"}. loglog_slope near 1 is diffusive,
    near 2 ballistic (too short a run), below 1 caged: report D only
    when it is near 1.

    Args:
        path: the trajectory on this machine.
        elements: the elements to follow, e.g. ["O"].
        dt_fs: time between consecutive frames in the file, fs (CP2K:
            TIMESTEP x MOTION/PRINT/TRAJECTORY/EACH/MD); a frames step
            multiplies it.
        cell: as for trajectory_rdf.
        frames: START:STOP:STEP; drop equilibration with e.g. "2000:".
        fit_ps: [start, end] of the fit window in ps; default 20-80 % of
            the lag range (10-40 % of the trajectory).
        points: lags returned, evenly spaced (the fit uses every lag).
        remove_drift: subtract the drift of the mean position of all atoms
            (unweighted).
    """
    return trajectory.msd(_local_file(path), elements, dt_fs, cell, frames, fit_ps,
                          points, remove_drift)


@mcp.tool()
def trajectory_vdos(path: str, dt_fs: float, elements: list[str] | None = None,
                    cell: Cell = None, frames: str = ":", max_wavenumber: float = 4000.0,
                    points: int = 400, n_peaks: int = 5) -> dict:
    """Vibrational density of states of an MD trajectory: the cosine
    transform of the velocity autocorrelation (every origin, lags up to
    half the run, half-Hann taper). Velocities are central differences of
    the unwrapped positions, not the MD velocities: peak positions are
    right, high-wavenumber intensities are damped (the "velocities" field
    says by how much). Unweighted by mass. Same input as trajectory_rdf;
    numpy only.

    Units: wavenumber in cm^-1; vdos normalised to unit area over 0 to
    max_wavenumber. Returns {"wavenumber" (`points` bin centres), "vdos",
    "peaks": [{wavenumber, relative}] (the n_peaks highest maxima at full
    resolution, relative to the highest), "resolution_cm-1" (1/run
    length), "elements", "natoms", "velocities", "method", "warnings"}.

    Args:
        path: the trajectory on this machine.
        dt_fs: time between consecutive frames in the file, fs (as for
            trajectory_msd); it sets the Nyquist limit, 16678/dt_fs cm^-1.
        elements: restrict to these elements, e.g. ["H"]; default all.
        cell: as for trajectory_rdf.
        frames: START:STOP:STEP; keep the step at 1 for high wavenumbers.
        max_wavenumber: top of the reported range, cm^-1.
        points: bins in the returned spectrum.
        n_peaks: most peaks to report.
    """
    return trajectory.vdos(_local_file(path), dt_fs, elements, cell, frames, max_wavenumber,
                           points, n_peaks)


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


@mcp.tool()
def colvar_fes(path: str, cvs: list[str], grid: list[list[float]], temperature: float,
               block_size: int, units: str = "kj/mol", bias: list[str] | None = None,
               frames: str = ":", out_path: str | None = None, overwrite: bool = False) -> dict:
    """Free energy F(s) = -kT ln p(s) along one to three collective
    variables from a PLUMED COLVAR file, with an error from block
    averaging (Bonomi's do_block_fes.py, same output format). Unbiased or
    restrained runs: no bias. Metadynamics: pass the bias column(s) (e.g.
    ["metad.bias"]) so each frame is weighted by exp(V/kT): use the
    COLVAR of a reweighting pass (plumed driver with the final bias, see
    hyogo's plumed_make_input mode "reweight"), not the one written during
    the run, where the bias was still growing. Writes fes.<block_size>.dat
    beside the COLVAR. Local only; numpy only.

    Choose block_size by trying several (e.g. 100, 500, 1000, 2000 frames)
    and taking the one where mean_error stops growing: shorter blocks are
    correlated and underestimate the error.

    Returns {"out", "cvs", "frames_used", "nblocks", "block_size", "kbt",
    "units", "weights", "effective_blocks", "minimum": {at, error},
    "max_error", "mean_error", "empty_bins", "warnings"}. F is not shifted:
    subtract the minimum to plot.

    Args:
        path: the COLVAR file on this machine.
        cvs: CV names from its "#! FIELDS" line, e.g. ["phi"] or ["phi", "psi"].
        grid: [min, max, nbins] per CV; grid points are min + k*(max-min)/(nbins-1).
            Every frame must fall within half a spacing of the grid.
        temperature: K, for kT.
        block_size: frames per block.
        units: the run's energy unit: "kj/mol" (PLUMED's default), "kcal/mol", "ev".
        bias: bias columns to reweight with, summed; default none (uniform weights).
        frames: START:STOP[:STEP] of the COLVAR rows, e.g. "1000:" to drop equilibration.
        out_path: default fes.<block_size>.dat beside the COLVAR.
        overwrite: replace out_path if it exists.
    """
    src = _local_file(path)
    dst = Path(out_path).expanduser() if out_path else src.with_name(f"fes.{block_size}.dat")
    if dst.resolve() == src.resolve():
        raise ValueError("out_path must differ from path")
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} exists; pass overwrite=true to replace it")
    if block_size < 1:
        raise ValueError("block_size is a positive number of frames")
    text, info = fes_mod.fes(src, cvs, grid, temperature, block_size, units, bias, frames)
    dst.write_text(text)
    return {"out": str(dst), **info}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    args, _ = parser.parse_known_args()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
