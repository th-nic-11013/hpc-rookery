"""hyogo MCP tools: cp2k.

hyogo/server.py loads every module in this folder. A new cp2k tool goes here;
a new group of tools is a new module beside this one.
"""
from pathlib import Path
from hyogo.cp2k import e0s as cp2k_e0s, input as cp2k_input, labels as cp2k_labels, output as cp2k_output, subsys
from hyogo.mcp_tools import mcp


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
                    pdos: dict | None = None, print_level: str = "LOW",
                    overwrite: bool = False) -> dict:
    """Write a CP2K run input that includes subsys.inc: a single point,
    a geometry or cell optimisation, or NVT MD, with a functional preset
    (XC, exact exchange, ADMM, D3). Tunables are @SET lines at the top.
    Local only; fs_upload it next to subsys.inc, then cp2k_check_input.

    Before it: cp2k_kinds(facility, elements, family=...) for the
    functional ("molopt" for revpbe/pbe, "uzh-scan" for r2scan/r2scan0/r2scanh,
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
    damping D3 with r2SCAN presets, purpose="label" without an explicit d3
    or for anything but a single point.

    For MLIP labels (energy, forces, stress in the .out): run_type="sp",
    purpose="label", d3="none" when the model adds D3 at inference.

    For MLIP E0s (isolated-atom energies at the labels' settings): one atom
    in a >= 15 A box from cp2k_make_subsys(periodic="NONE"), then
    run_type="sp", purpose="atom", multiplicity=<the free atom's>, and the
    labels' functional, kinds, cutoff and rel_cutoff. Written as
    e0-<El>-m<multiplicity>.inp (PROJECT the same). Run the other plausible
    multiplicities too; cp2k_make_e0s keeps the lowest.

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

    Returns {"out", "run_type", "functional", "d3", "scf", "purpose",
    "print_level", "label" ({prints, in, next} for purpose="label"), "atom"
    ({element, multiplicity, box_A, project, next} for purpose="atom"), "wfn"
    (the restart file a sp writes, or null), "restart" ({set, file, wfn}
    for geo_opt/cell_opt/md_nvt, else null), "opt", "guess" ({wfn,
    from_project, checked, final_xyz} with guess_from, else null), "settings":
    {CUTOFF, HFX_RADIUS, WALLTIME, ...}, "files": data files the build
    needs, "warnings"}; read the warnings to the user (hybrid cost, HFX
    radius, open shell, assumed walltime).

    Args:
        run_type: "sp", "geo_opt", "cell_opt" or "md_nvt".
        functional: "revpbe" (the benchmarked default), "pbe", "r2scan",
            "pbe0", "hse06", "r2scan0" (25 % exact exchange) or
            "r2scanh" (10 %).
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
        d3: "zero", "bj" or "none"; default zero for revpbe, bj otherwise.
            "none" leaves dispersion out: only for MLIP labels whose model
            adds D3 at inference (warned).
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
            just below half the narrowest width (pbe0, r2scan0, r2scanh), or for
            hse06 to the range where erfc(0.11 r)/r reaches eps_schwarz,
            capped below half the width. screen_on_initial_p defaults to
            true only with guess_wfn.
        print_gap: print the HOMO-LUMO gap.
        pdos: projected DOS per atomic kind (single point only):
            {"nlumo": 20 (unoccupied MOs to include; -1 all), "components":
            false (s p d f; true splits px py pz, d-2 ...)}. Writes
            <PROJECT>-k<n>-1.pdos per kind; read them with yagura pdos_read
            for the band-edge character.
        save_wfn: write the SCF wavefunction restart (sp), e.g. to start a
            hybrid from a GGA run.
        stress: analytical stress tensor in a single point (GPa).
        precision: {eps_default (1e-12), eps_pgf_orb} for &QS.
        guess_wfn: wavefunction to start from (SCF_GUESS RESTART), a path
            on the cluster relative to the run folder or absolute; it must
            use the same basis. Start hybrids from a converged GGA run.
        purpose: "result" (default), "guess", "label" or "atom". "guess": a GGA
            single point (RUN_TYPE ENERGY, wavefunction saved) in a hybrid's
            basis and potentials, labelled as not a result; its only use is
            the hybrid's guess_wfn. "label": a single point for MLIP training
            labels, with energy, forces and analytical stress all printed in
            the .out (no separate files); d3 must be given (refused when
            omitted). Read the outputs with cp2k_make_labels. "atom": an
            isolated-atom E0 (one atom, periodic NONE, multiplicity
            required, no stress, no D3); read with cp2k_make_e0s.
        print_level: GLOBAL PRINT_LEVEL, "LOW" (default; prints everything
            cp2k_parse_output and cp2k_make_labels read) or "MEDIUM".
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
                                       guess_from, guess_dir, pdos, print_level)
    name = (f"{project}.inp" if project else "guess.inp" if purpose == "guess"
            else f"{info['atom']['project']}.inp" if purpose == "atom"
            else cp2k_input.RUN_TYPES[run_type][3])
    dst = Path(out_path).expanduser() if out_path else Path(subsys["out"]).expanduser().with_name(name)
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} exists; pass overwrite=true to replace it")
    dst.write_text(text)
    return {"out": str(dst), **info}


@mcp.tool()
def cp2k_make_labels(frames: list, out_path: str, head: str | None = None,
                     config_type: str | None = None, require_stress: bool = True,
                     allow_untested_version: bool = False, overwrite: bool = False) -> dict:
    """Write MLIP training labels (one extended-XYZ file, MACE's keys) from
    CP2K single points: each run's .out plus the subsys.inc it ran on.
    Local only: the .out is the record, this file is rebuilt from it.

    Before it: cp2k_make_input("sp", ..., purpose="label", d3=...) for each
    frame, the runs, then fs_download each .out and its subsys.inc.

    Writes per frame: species, positions and Lattice from subsys.inc;
    REF_energy (eV), REF_forces (eV/A), REF_stress (eV/A^3, ASE sign,
    Voigt xx yy zz yz xz xy; CP2K's printed GPa tensor is pressure-like, so
    the sign flips), pbc, config_type, head, source (path as given),
    source_sha256, cp2k_version, project, dispersion_eV (D3 energy, only
    when the run had D3). D3 is never subtracted: the .out has its energy
    but not its forces or stress.

    Refuses: an unfinished run, a run type other than ENERGY_FORCE, an
    unconverged SCF, a missing force printout, force rows or .out atoms
    that don't match subsys.inc, a missing stress tensor (unless
    require_stress=false), a CP2K version whose .out layout isn't tested
    (unless allow_untested_version=true), and frames that mix D3 with
    no-D3 runs.

    Returns {"out", "frames", "natoms", "d3" ("included" or "none"),
    "versions", "stress", "warnings"}; read the warnings to the user
    (labels that include D3, CP2K's own warnings, duplicates).

    Args:
        frames: [{"out": local .out, "subsys": local subsys.inc,
            "config_type": optional per frame}], in the order to write.
        out_path: the .extxyz to write.
        head: MACE head name for every frame (multihead fine-tuning).
        config_type: default config_type for frames without their own.
        require_stress: refuse frames without the stress tensor.
        allow_untested_version: accept a CP2K version the tests don't
            cover (warned).
        overwrite: replace out_path if it exists.
    """
    import hashlib
    if not isinstance(frames, list) or not all(isinstance(f, dict) for f in frames):
        raise ValueError('frames is a list of {"out": <.out>, "subsys": <subsys.inc>}')
    items = []
    for f in frames:
        if set(f) - {"out", "subsys", "config_type"} or not f.get("out") or not f.get("subsys"):
            raise ValueError(f'each frame is {{"out", "subsys", "config_type"?}}, got {sorted(f)}')
        out, sub = Path(f["out"]).expanduser(), Path(f["subsys"]).expanduser()
        for x in (out, sub):
            if not x.is_file():
                raise ValueError(f"{x} is not a file on this machine; download it first "
                                 "(fs_download), with the subsys.inc from the same run folder")
        raw = out.read_bytes()
        items.append({"out_text": raw.decode(errors="replace"),
                      "subsys_text": sub.read_text(errors="replace"), "source": f["out"],
                      "sha256": hashlib.sha256(raw).hexdigest(),
                      "config_type": f.get("config_type", config_type)})
    dst = Path(out_path).expanduser()
    if dst.suffix not in (".extxyz", ".xyz"):
        raise ValueError("out_path ends in .extxyz (or .xyz)")
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} exists; pass overwrite=true to replace it")
    text, info = cp2k_labels.make_labels(items, head, require_stress, allow_untested_version)
    dst.write_text(text)
    return {"out": str(dst), **info}


@mcp.tool()
def cp2k_make_e0s(runs: list, out_path: str, label: dict | None = None,
                  allow_untested_version: bool = False, overwrite: bool = False) -> dict:
    """Write MLIP E0s (isolated-atom energies, eV) from CP2K single points:
    {atomic number: eV} json for mace_make_finetune(e0s=<this path>), plus
    <stem>.provenance.json beside it. Local only.

    Before it: per element, cp2k_make_subsys(periodic="NONE") on one atom
    in a >= 15 A box, cp2k_make_input("sp", <labels' functional>, ...,
    purpose="atom", multiplicity=...) with the labels' kinds, cutoff and
    rel_cutoff; run them, then fs_download each .out. The .inp and
    subsys.inc are the local files you wrote (the .out at PRINT_LEVEL LOW
    doesn't echo the functional, cutoffs or multiplicity).

    Per element it keeps the lowest-energy multiplicity (the others are
    listed with their energy above it) and, for a multiplicity run in two
    boxes, the larger box (the difference is reported). Energies use the
    Ha -> eV constant cp2k_make_labels uses.

    Refuses: an unfinished or unconverged run, more than one atom, a
    periodic box, smeared occupations, an input not written with
    purpose="atom", runs whose functional block, basis or potential files,
    cutoffs, precision or CP2K version differ, two bases for one element,
    the same element/multiplicity/box twice, an untested CP2K version
    (unless allow_untested_version=true), and, with label, any of those
    settings or an element's basis/potential differing from the labels'.

    Returns {"out", "provenance", "e0s" ({"Z": eV}), "per_element" ({El:
    {Z, multiplicity, box_A, energy_ha, e0_ev, kind, source, sha256,
    others, box_check_ev}}), "settings", "label_checked", "runs",
    "warnings"}; read the warnings to the user (ground state not reached,
    box difference > 1 meV, multiplicity not cross-checked, elements of the
    labels without an E0).

    Args:
        runs: [{"out": local .out, "inp": local .inp, "subsys": local
            subsys.inc}], one per isolated-atom run.
        out_path: the .json to write (e.g. e0s.json).
        label: {"inp", "subsys", "out"?} of one label run, to check the
            atoms used its settings; strongly advised.
        allow_untested_version: accept a CP2K version the tests don't
            cover (warned).
        overwrite: replace out_path (and its provenance) if they exist.
    """
    import hashlib
    import json as _json
    if not isinstance(runs, list) or not all(isinstance(r, dict) for r in runs):
        raise ValueError('runs is a list of {"out": <.out>, "inp": <.inp>, "subsys": <subsys.inc>}')

    def _local(path: str, what: str) -> Path:
        p = Path(path).expanduser()
        if not p.is_file():
            raise ValueError(f"{p} ({what}) is not a file on this machine; "
                             + ("download it first (fs_download)" if what == ".out" else
                                "pass the local file cp2k_make_input/cp2k_make_subsys wrote"))
        return p

    items = []
    for r in runs:
        if set(r) != {"out", "inp", "subsys"}:
            raise ValueError(f'each run is {{"out", "inp", "subsys"}}, got {sorted(r)}')
        raw = _local(r["out"], ".out").read_bytes()
        items.append({"out_text": raw.decode(errors="replace"), "source": r["out"],
                      "sha256": hashlib.sha256(raw).hexdigest(),
                      "inp_text": _local(r["inp"], ".inp").read_text(errors="replace"),
                      "subsys_text": _local(r["subsys"], "subsys.inc").read_text(errors="replace")})
    lab = None
    if label is not None:
        if not isinstance(label, dict) or not {"inp", "subsys"} <= set(label) \
                or set(label) - {"inp", "subsys", "out"}:
            raise ValueError('label is {"inp", "subsys", "out"?} of one label run')
        lab = {"inp_text": _local(label["inp"], ".inp").read_text(errors="replace"),
               "subsys_text": _local(label["subsys"], "subsys.inc").read_text(errors="replace")}
        if label.get("out"):
            lab["out_text"] = _local(label["out"], ".out").read_text(errors="replace")
    dst = Path(out_path).expanduser()
    if dst.suffix != ".json":
        raise ValueError("out_path ends in .json")
    prov = dst.with_name(dst.stem + ".provenance.json")
    for x in (dst, prov):
        if x.exists() and not overwrite:
            raise ValueError(f"{x} exists; pass overwrite=true to replace it")
    _, info = cp2k_e0s.make_e0s(items, lab, allow_untested_version)
    dst.write_text(_json.dumps(info["e0s"], indent=1) + "\n")
    prov.write_text(_json.dumps({k: v for k, v in info.items() if k != "warnings"},
                                indent=1) + "\n")
    return {"out": str(dst), "provenance": str(prov), **info}


@mcp.tool()
def cp2k_parse_output(out_path: str, vectors: bool = False) -> dict:
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
    "energy_ha", "gap_ev", "dispersion_ha" (the D3 energy CP2K prints in
    each SCF summary, last one; null without D3), "forces": {max, rms,
    max_atom, natoms, vectors (natoms x [x, y, z], with vectors=true)}
    (Ha/bohr),
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
    rookery's cp2k_record_from_run. For MLIP training labels, use
    cp2k_make_labels, which calls this with vectors=true.

    Args:
        out_path: the CP2K output on this machine; ~ is expanded.
        vectors: also return the per-atom force vectors (large: 3 floats
            per atom).
    """
    src = Path(out_path).expanduser()
    if not src.is_file():
        raise ValueError(f"{src} is not a file on this machine. For an output on a "
                         "cluster, download it first (fs_download).")
    return {"path": str(src), **cp2k_output.parse(src.read_text(errors="replace"), vectors)}
