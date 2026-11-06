"""Entry point for the hyogo MCP server (hyogo-mcp).

Tools take local paths and the facts other servers returned, never a
facility: hyogo does not reach a cluster. The agent moves files with
rookery's fs_upload / fs_download.
"""
import argparse
from pathlib import Path

from hyogo.cp2k import input as cp2k_input, output as cp2k_output, subsys
from hyogo.mcp_server import MCPServer

mcp = MCPServer("hyogo-mcp")


@mcp.tool()
def cp2k_make_subsys(xyz_path: str, kinds: dict, out_path: str | None = None,
                     periodic: str = "XYZ", charge: int = 0, symmetry: str | None = None,
                     overwrite: bool = False) -> dict:
    """Write a CP2K &SUBSYS include (subsys.inc: &CELL, &COORD, one &KIND
    per element) from an extended xyz on this machine. Run inputs include
    it inside &SUBSYS with `@INCLUDE subsys.inc`. Local only; fs_upload
    the result next to the run input.

    Before it: structure_check the xyz (and structure_stretch_xh on
    `short_xh`), then cp2k_kinds(facility, elements) for the build that
    will run it; pass that result (or its `kinds`) here.

    For a hybrid (ADMM), call cp2k_kinds with aux_basis: each kind then
    carries `aux_basis` and gets a `BASIS_SET AUX_FIT` line.

    For a cell_opt that keeps the lattice (KEEP_SYMMETRY), pass symmetry:
    it writes &CELL SYMMETRY after checking the cell vectors have it.

    Returns {"out", "formula", "natoms", "electrons", "charge", "periodic",
    "symmetry", "widths", "kinds": {element: potential}, "aux_basis": {element: name},
    "warnings"}; read the warnings to the user (thin cell, odd electron
    count, PERIODIC NONE). Pass the whole result to cp2k_make_input.

    Args:
        xyz_path: extended xyz (Lattice= on line 2) on this machine.
        kinds: cp2k_kinds' result, or its `kinds` mapping
            {element: {q, basis, potential[, aux_basis]}}.
        out_path: where to write; default subsys.inc beside the xyz.
        periodic: "XYZ" (crystals, liquids) or "NONE" (an isolated molecule).
        charge: total charge, for the electron count only.
        symmetry: CP2K &CELL SYMMETRY: "CUBIC", "TETRAGONAL_AB",
            "TETRAGONAL_AC", "TETRAGONAL_BC", "ORTHORHOMBIC" or
            "RHOMBOHEDRAL"; refused when the cell doesn't have it.
        overwrite: replace out_path if it exists.
    """
    src = Path(xyz_path).expanduser()
    if not src.is_file():
        raise ValueError(f"{src} is not a file on this machine. For a file on a "
                         "cluster, download it first (fs_download).")
    dst = Path(out_path).expanduser() if out_path else src.with_name("subsys.inc")
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} exists; pass overwrite=true to replace it")
    table = kinds.get("kinds", kinds) if isinstance(kinds, dict) else kinds
    if not isinstance(table, dict):
        raise ValueError("kinds is cp2k_kinds' result or its `kinds` mapping")
    text, info = subsys.subsys(src.read_text(), src.name, table, periodic, charge, symmetry)
    dst.write_text(text)
    return {"out": str(dst), **info}


@mcp.tool()
def cp2k_make_input(run_type: str, functional: str, subsys: dict, kinds: dict | None = None,
                    out_path: str | None = None, project: str | None = None,
                    cutoff: int | None = None, rel_cutoff: int = 60, d3: str | None = None,
                    scf: str = "ot", multiplicity: int | None = None,
                    duration: str | None = None, md: dict | None = None, opt: dict | None = None,
                    hfx: dict | None = None, print_gap: bool = False, save_wfn: bool = False,
                    stress: bool = False, precision: dict | None = None,
                    guess_wfn: str | None = None, purpose: str = "result",
                    guess_from: dict | None = None, guess_dir: str | None = None,
                    overwrite: bool = False) -> dict:
    """Write a CP2K run input that includes subsys.inc: a single point,
    a geometry or cell optimisation, or NVT MD, with a functional preset
    (XC, exact exchange, ADMM, D3). Tunables are @SET lines at the top.
    Local only; fs_upload it next to subsys.inc, then cp2k_check_input.

    Before it: cp2k_kinds(facility, elements, family=...) for the
    functional ("molopt" for revpbe/pbe, "uzh-scan" for r2scan/r2scan0,
    "uzh-pbe0" for pbe0/hse06; hybrids also aux_basis="admm-dzp"), then
    cp2k_make_subsys with that result. Pass both results here. For
    duration, use launch_plan's estimate.suggested_duration.

    Hybrids get the CP2K settings that keep HFX correct: the ADMM
    correction matched to the HF operator (EXCH_CORRECTION_FUNC DEFAULT),
    MIN_PAIR_LIST_RADIUS -1, force screening as tight as the energy, and
    an operator range below half the narrowest cell width (Gamma-point
    HFX), capped there with a warning for hse06.

    Refuses: potentials of the wrong family, a hybrid without AUX_FIT
    bases, an HFX radius at or above half the narrowest cell width, zero
    damping D3 with r2SCAN presets.

    To start a hybrid from a GGA wavefunction in the hybrid's own basis:
    first cp2k_make_input("sp", "pbe", <the hybrid's subsys>, <its kinds>,
    purpose="guess"), run it, then the hybrid with guess_wfn set to that
    result's `wfn`.

    To start from a previous run (a follow-up on its relaxed cell, or a
    hybrid from a GGA run in the same basis): pass that run's
    cp2k_parse_output result as guess_from and its folder relative to the
    new one as guess_dir (e.g. "../hse06-cellopt"). That sets guess_wfn to
    its <project>-RESTART.wfn, and refuses when the wavefunction can't fit:
    other atom or electron counts, or other basis or potential files. The
    structure is separate: fs_download its files.final_xyz, then
    structure_check and cp2k_make_subsys.

    geo_opt, cell_opt and md_nvt inputs start with @SET RESTART 0. After a
    walltime stop, set it to 1 and resubmit: the run continues from its
    <project>-1.restart and its own wavefunction.

    Returns {"out", "run_type", "functional", "d3", "scf", "purpose", "wfn"
    (the restart file a sp writes, or null), "restart" ({set, file, wfn}
    for geo_opt/cell_opt/md_nvt, else null), "opt", "guess" ({wfn,
    from_project, checked, final_xyz} with guess_from, else null), "settings":
    {CUTOFF, HFX_RADIUS, WALLTIME, ...}, "files": data files the build
    needs, "warnings"}; read the warnings to the user (hybrid cost, HFX
    radius, open shell, assumed walltime).

    Args:
        run_type: "sp", "geo_opt", "cell_opt" or "md_nvt".
        functional: "revpbe" (the benchmarked default), "pbe", "r2scan",
            "pbe0", "hse06" or "r2scan0".
        subsys: cp2k_make_subsys' result (widths, electrons, charge, kinds).
        kinds: cp2k_kinds' result, for the data file names; may be
            omitted only for revpbe/pbe on BASIS_MOLOPT.
        out_path: where to write; default beside subsys.inc, named as the
            skills' templates (single_point.inp, geo_opt.inp, cell_opt.inp,
            aimd_nvt.inp), or <project>.inp when project is given.
        project: CP2K PROJECT name; default sp, opt, cellopt or md.
        cutoff: MGRID CUTOFF in Ry; default 450 for GGA, 600 for r2SCAN,
            hybrids and cell_opt.
        rel_cutoff: MGRID REL_CUTOFF in Ry.
        d3: "zero" or "bj"; default zero for revpbe, bj otherwise.
        scf: "ot" (insulators) or "smear" (metals, small gaps).
        multiplicity: spin multiplicity; default 1, or 2 for odd electrons.
        duration: job duration "HH:MM:SS"; WALLTIME leaves a margin.
        md: {temperature, timestep, steps, timecon} for md_nvt.
        opt: for geo_opt and cell_opt, {max_iter (200), max_force (4.5e-4
            Ha/bohr), rms_force (CP2K's default), keep_angles,
            keep_symmetry}. The keep flags are cell_opt only, and
            keep_symmetry needs cp2k_make_subsys' symmetry.
        hfx: for hybrids, {radius, memory_mb (3000, MiB per MPI rank),
            eps_schwarz (1e-6), screen_on_initial_p}. radius defaults to
            just below half the narrowest width (pbe0, r2scan0), or for
            hse06 to the range where erfc(0.11 r)/r reaches eps_schwarz,
            capped below half the width. screen_on_initial_p defaults to
            true only with guess_wfn.
        print_gap: print the HOMO-LUMO gap.
        save_wfn: write the SCF wavefunction restart (sp), e.g. to start a
            hybrid from a GGA run.
        stress: analytical stress tensor in a single point (GPa).
        precision: {eps_default (1e-12), eps_pgf_orb} for &QS.
        guess_wfn: wavefunction to start from (SCF_GUESS RESTART), a path
            on the cluster relative to the run folder or absolute; it must
            use the same basis. Start hybrids from a converged GGA run.
        purpose: "result" (default) or "guess": a GGA single point (RUN_TYPE
            ENERGY, wavefunction saved) in a hybrid's basis and potentials,
            labelled as not a result. Its only use is the hybrid's guess_wfn.
        guess_from: a previous run's cp2k_parse_output result, unchanged;
            sets guess_wfn from it (don't pass both).
        guess_dir: that run's folder relative to this run's folder;
            default "." (the same folder).
        overwrite: replace out_path if it exists.
    """
    if not isinstance(subsys, dict) or not subsys.get("out"):
        raise ValueError("subsys is cp2k_make_subsys' result; call it first")
    files = kinds.get("files") if isinstance(kinds, dict) else None
    if kinds is not None and files is None:
        raise ValueError("kinds is cp2k_kinds' full result (it carries `files`)")
    text, info = cp2k_input.make_input(run_type, functional, subsys, files, project, cutoff,
                                       rel_cutoff, d3, scf, multiplicity, duration, md, opt, hfx,
                                       print_gap, save_wfn, stress, precision, guess_wfn, purpose,
                                       guess_from, guess_dir)
    name = (f"{project}.inp" if project else "guess.inp" if purpose == "guess"
            else cp2k_input.RUN_TYPES[run_type][3])
    dst = Path(out_path).expanduser() if out_path else Path(subsys["out"]).expanduser().with_name(name)
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} exists; pass overwrite=true to replace it")
    dst.write_text(text)
    return {"out": str(dst), **info}


@mcp.tool()
def cp2k_parse_output(out_path: str) -> dict:
    """Read a CP2K output (.out) on this machine: did the run finish and
    converge, what did it print, and what did it cost. Writes nothing.
    Use it instead of grepping an output.

    Before it: get_job_status (completed, or to see a killed job), then
    fs_download the .out from the run folder.

    Values are the last ones printed (the final step of an optimisation),
    null when the output doesn't print them: single points print no cell,
    and an optimisation prints forces and stress only if asked to.

    Returns {"path", "version", "project", "run_type", "finished",
    "started" (YYYY-MM-DD), "ranks", "threads" (per rank), "natoms",
    "electrons", "scf": {evaluations, not_converged, steps, outer},
    "energy_ha", "gap_ev", "forces": {max, rms, max_atom} (Ha/bohr),
    "stress_gpa": {tensor, third_trace}, "cell": {a, b, c, alpha, beta,
    gamma, volume} (A, degrees, A^3), "opt": {converged, steps,
    max_iter_hit, criteria: {max_step, rms_step, max_gradient,
    rms_gradient, pressure_dev_bar: {value, limit, converged}},
    internal_pressure_bar, energy_ha, step_times_s (OPT| Used time per
    evaluation, step 0 first)}, "md": {steps, s_per_step}, "cost":
    {cp2k_s, peak_mib, top_timers, hfx: {eri_ram_mib, eri_disk_mib,
    on_the_fly, max_program_mib}}, "files": {final_xyz, final_cif (as
    CP2K named them after a converged optimisation, else null), wfn
    (<project>-RESTART.wfn), restart (<project>-1.restart for opt/md),
    basis (the first basis file), potential}: names inside the run
    folder, for chaining the next run (cp2k_make_input guess_from),
    "warnings"}. Read the warnings to the
    user: an unfinished output, unconverged SCF, an optimisation stopped
    by WALLTIME (continue it with @SET RESTART 1) or MAX_ITER, and CP2K's
    own warnings. For a benchmark record, pass the whole result to
    rookery's cp2k_record_from_run.

    Args:
        out_path: the CP2K output on this machine; ~ is expanded.
    """
    src = Path(out_path).expanduser()
    if not src.is_file():
        raise ValueError(f"{src} is not a file on this machine. For an output on a "
                         "cluster, download it first (fs_download).")
    return {"path": str(src), **cp2k_output.parse(src.read_text(errors="replace"))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    args, _ = parser.parse_known_args()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
