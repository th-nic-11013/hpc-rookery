"""hyogo MCP tools: vasp.

hyogo/server.py loads every module in this folder. A new vasp tool goes here;
a new group of tools is a new module beside this one.
"""
from pathlib import Path
from hyogo.vasp import incar as vasp_incar, litsearch_check, output as vasp_output
from hyogo.mcp_tools import _read_local, mcp


def _beside(src: Path, name: str) -> Path | None:
    """name (or name.gz) next to src, if it exists."""
    for cand in (src.with_name(name), src.with_name(name + ".gz")):
        if cand.is_file():
            return cand
    return None


@mcp.tool()
def vasp_make_incar(poscar_path: str, base_incar: str | None = None,
                    settings: dict | None = None, magmom: dict | None = None,
                    magmom_sites: dict | None = None, nupdown: float | None = None,
                    parallel: dict | None = None, nbands: int | None = None,
                    nbands_from: str | None = None, pre: dict | None = None,
                    out_path: str | None = None, overwrite: bool = False,
                    vasp_version: str | None = None) -> dict:
    """Write a VASP INCAR by editing a base INCAR (or from scratch), with
    the checks that matter for spin and parallel settings. The base file's
    layout and comments are kept; changed keys are replaced in place.
    Local only; fs_upload the result into the run folder.

    Before it: fs_download the base INCAR (e.g. the one a reference or
    label run used) and the run's POSCAR. To keep the band count of a
    reference run, also fs_download its OUTCAR and pass it as nbands_from.

    What it checks:
    - MAGMOM is written in the POSCAR's species order and must count
      every atom (an INCAR's existing MAGMOM is checked too).
    - MAGMOM or NUPDOWN need ISPIN = 2.
    - NBANDS: VASP rounds it up, silently, to a multiple of the band
      groups (ranks / (KPAR x NCORE)). With parallel.ranks given, a
      pinned NBANDS that doesn't divide is refused, and an unpinned one
      gets a warning.
    - NUPDOWN in a result fixes its total moment: right for a half-metal
      or a non-magnetic system, a bias for a metallic magnet. For those,
      pass pre to constrain, then release.
    - Every tag and value against the VASP documentation, when lit-search
      is set up (see vasp_check_incar): unknown tags, values that don't
      fit, tags newer than vasp_version. Its findings join the warnings.

    pre (constrain-then-release) also writes INCAR.pre beside the INCAR:
    the same INCAR with NUPDOWN fixed, a loose EDIFF, its own NELM and
    LWAVE on. The main INCAR gets ISTART = 1. Run INCAR.pre in its own
    folder first, then copy its WAVECAR into the main run's folder.

    Returns {"out", "pre_out" (INCAR.pre path or null), "natoms",
    "species": [[element, count]], "magmom" (the value written),
    "total_magmom", "nbands", "band_groups", "changed": {KEY: [old, new]},
    "pre": {"changed": ...} or null, "incar_check" (vasp_check_incar's
    result for each file written), "warnings"}. Read the warnings to the
    user.

    Args:
        poscar_path: the run's POSCAR on this machine (species line needed).
        base_incar: INCAR to start from; omit to write one from settings only.
        settings: {TAG: value}; None removes the tag. Booleans become
            .TRUE./.FALSE. MAGMOM, NUPDOWN, NCORE, KPAR and NBANDS go
            through their own arguments.
        magmom: {element: initial moment in uB}; elements left out get 0.
        magmom_sites: {"1-based atom index": moment}, applied after magmom
            (e.g. one site flipped).
        nupdown: fixed total moment (NUPDOWN); -1 releases it.
        parallel: {ranks, ncore, kpar}: writes NCORE and KPAR when given,
            and checks NBANDS against ranks / (kpar x ncore).
        nbands: NBANDS to pin.
        nbands_from: an OUTCAR (.gz ok) whose NBANDS to pin, e.g. the
            reference run's, so a new layout keeps its band count.
        pre: {nupdown (required), ediff ("1E-4"), nelm (120)} for a
            constrained pre-run.
        out_path: where to write; default INCAR beside the POSCAR.
        overwrite: replace out_path (and INCAR.pre) if they exist.
        vasp_version: the VASP that will run it (e.g. "6.4.3"), so tags it
            does not have yet are flagged.
    """
    psrc, praw = _read_local(poscar_path, "POSCAR")
    base = ""
    if base_incar:
        _, braw = _read_local(base_incar, "base INCAR")
        base = braw.decode("latin-1")
    if nbands is not None and nbands_from:
        raise ValueError("pass nbands or nbands_from, not both")
    if nbands_from:
        _, oraw = _read_local(nbands_from, "OUTCAR for NBANDS")
        nbands = vasp_incar.nbands_from_outcar(oraw.replace(b"\x00", b"").decode("latin-1"))
    dst = Path(out_path).expanduser() if out_path else psrc.with_name("INCAR")
    pre_dst = dst.with_name(dst.name + ".pre") if dst.name != "INCAR" else dst.with_name("INCAR.pre")
    for f in (dst, pre_dst) if pre is not None else (dst,):
        if f.exists() and not overwrite:
            raise ValueError(f"{f} exists; pass overwrite=true to replace it")
    text, pre_text, info = vasp_incar.make_incar(base, praw.decode("latin-1"), settings, magmom,
                                                 magmom_sites, nupdown, parallel, nbands, pre)
    dst.write_text(text)
    if pre_text is not None:
        pre_dst.write_text(pre_text)
    checks = {}
    for f in (dst, pre_dst) if pre_text is not None else (dst,):
        checks[f.name] = litsearch_check.check(f, vasp_version)
        if checks[f.name]["ran"]:  # a missing lit-search is in incar_check, not a warning
            info["warnings"] = [*info.get("warnings", []),
                                *litsearch_check.as_warnings(checks[f.name], f.name)]
    return {"out": str(dst), "pre_out": str(pre_dst) if pre_text is not None else None,
            **info, "incar_check": checks}


@mcp.tool()
def vasp_check_incar(incar_path: str, vasp_version: str | None = None) -> dict:
    """Check an INCAR on this machine against the VASP documentation:
    every tag is a real INCAR tag (close matches suggested for a typo),
    its value fits the tag's type or allowed words, no tag is newer than
    vasp_version, no tag is set twice. Writes nothing.

    The check is lit-search's (`lit-search reference check VASP`), run as
    a separate program over the VASP wiki's INCAR tags kept as verified
    records. Set HYOGO_LITSEARCH (the lit-search command) and
    HYOGO_LITSEARCH_LIBRARY (the library holding the VASP reference) in
    this server's env; without them it says it did not run.

    Limits: tags the wiki types as [string] (ALGO) have no word list, so
    a misspelt word there passes; a finding marked "unverified" comes from
    a reference entry no person has reviewed yet.

    Returns {"path", "ran", "why" (when it did not run), "findings":
    [{line, tag, value, level ("error"|"warning"), message}], "errors"}.
    Read the findings to the user.

    Args:
        incar_path: the INCAR on this machine; ~ is expanded.
        vasp_version: the VASP that will run it, e.g. "6.4.3".
    """
    src, _ = _read_local(incar_path, "INCAR")
    return {"path": str(src), **litsearch_check.check(src, vasp_version)}


@mcp.tool()
def vasp_parse_output(outcar_path: str, oszicar_path: str | None = None,
                      poscar_path: str | None = None, arrays: bool = False) -> dict:
    """Read a VASP OUTCAR on this machine (plain or .gz): did the run
    finish and converge, what did it print, what layout did it run with
    and what did it cost. Writes nothing. Use it instead of grepping.

    Before it: get_job_status (completed, or to see a killed job), then
    fs_download the OUTCAR, and the OSZICAR and POSCAR beside it: they are
    read automatically when they sit next to the OUTCAR.

    "converged" means every ionic step's SCF reached EDIFF (VASP's own
    "aborting loop because EDIFF is reached" line), there is a TOTEN, and
    the last force block has a row per atom. VASP exits 0 and prints
    energies and forces when the SCF hits NELM; those are not results.

    NUL bytes (holes left by a full disk) are stripped and counted; when
    they ate the header, the POSCAR supplies species and the atom count.

    Returns {"path", "version", "flavour" (complex, gamma-only, ...),
    "finished", "nul_bytes", "natoms", "species", "potcars", "params":
    {NBANDS, NKPTS, ISPIN, NELM, EDIFF, NELECT, NUPDOWN, ENCUT, ISMEAR,
    SIGMA, IBRION, NSW, ISTART, LORBIT}, "incar" (the INCAR as VASP echoed
    it), "layout": {ranks, nodes, threads, kpar, ncore, band_groups},
    "scf": {ionic_steps, steps, converged, nelm, nelm_hit}, "converged",
    "energy": {toten, sigma0, without_entropy, per_atom_toten} (eV),
    "e_fermi", "magnetization": {total, source, outcar_last_step, sites:
    {by_element: {el: {n, min, max, sum, opposed}}, largest: [[atom,
    el, m]]}} (null for ISPIN 1; sites need LORBIT >= 10), "forces":
    {complete, rows, max, max_atom, rms, drift} (eV/A), "stress_kb":
    {xx_yy_zz_xy_yz_zx, pressure}, "cost": {elapsed_s, loop_s,
    max_memory_mb}, "oszicar": {steps, F, E0, mag}, "messages" (VASP's
    warning, advice and error boxes), "warnings"}. With arrays: also
    "positions", "forces.vectors" and "magnetization.site_moments".

    "opposed" counts sites of an element whose moment (>= 0.5 uB) points
    against that element's net moment: a flipped site.

    Read the warnings to the user: NELM hit, unfinished or cut files, NUL
    holes, an OSZICAR from another run, missing site moments, and VASP's
    own boxes.

    Args:
        outcar_path: the OUTCAR on this machine; ~ is expanded.
        oszicar_path: default the OSZICAR (or .gz) beside the OUTCAR, if any.
        poscar_path: default the POSCAR beside the OUTCAR, if any.
        arrays: include per-atom positions, forces and moments.
    """
    src, raw = _read_local(outcar_path, "OUTCAR")
    osz = Path(oszicar_path).expanduser() if oszicar_path else _beside(src, "OSZICAR")
    pos = Path(poscar_path).expanduser() if poscar_path else _beside(src, "POSCAR")
    osz_text = _read_local(str(osz), "OSZICAR")[1].decode("latin-1") if osz else None
    pos_text = _read_local(str(pos), "POSCAR")[1].decode("latin-1") if pos else None
    r = vasp_output.parse(raw, osz_text, pos_text, arrays)
    return {"path": str(src), "oszicar_path": str(osz) if osz else None,
            "poscar_path": str(pos) if pos else None, **r}
