"""Entry point for the hyogo MCP server (hyogo-mcp).

Tools take local paths and the facts other servers returned, never a
facility: hyogo does not reach a cluster. The agent moves files with
rookery's fs_upload / fs_download.
"""
import argparse
from pathlib import Path

from hyogo.cp2k import input as cp2k_input, output as cp2k_output, subsys
from hyogo.lammps import input as lammps_input, log as lammps_log
from hyogo.plumed import input as plumed_input
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
            just below half the narrowest width (pbe0, r2scan0, r2scanh), or for
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


@mcp.tool()
def lammps_make_input(data: dict, model: str, mode: str, steps: dict,
                      temperature: float = 300.0, timestep_fs: float = 0.5,
                      tdamp_fs: float | None = None, seed: int = 4928459, device: str = "cpu",
                      pressure_bar: float = 1.0, barostat: str = "iso",
                      pdamp_fs: float | None = None, thermo_every: int | None = None,
                      dump_every: int = 0, restart_every: int = 0,
                      restart_from: str | None = None, plumed_file: str | None = None,
                      data_name: str | None = None, out_path: str | None = None,
                      overwrite: bool = False) -> dict:
    """Write a LAMMPS input for MD with a machine-learned potential
    through pair_style metatomic (MACE and other metatomic exports), metal
    units, as HOKUSAI's MLMD benchmark ran it. Local only; fs_upload it,
    the .data file and (with plumed_file) the PLUMED input to one run
    folder. The model path is on the cluster.

    Modes: "valid" (NVT then NVE: is the model stable, and does it conserve
    energy?), "time" (untimed warm-up, then timed NVT), "nvt", "npt" and
    "nve" (production; may dump traj.xyz, write restart files, and couple
    PLUMED). Every fresh start prints the step-0 energy (`run 0`) before
    velocities are drawn, so it can be checked against an ASE reference.

    Before it: yagura structure_to_lammps_data (pass its whole result as
    data); for a bias, plumed_make_input. After the run: fs_download
    log.lammps and lammps_parse_log.

    Refuses: a timestep above 1 fs with hydrogen (0.5 fs is benchmarked),
    a model that isn't a .pt export, steps that don't match the mode,
    dumps/restarts/PLUMED in the benchmark modes.

    Returns {"out", "mode", "steps", "total_steps", "simulated_ps",
    "timestep_fs", "temperature", "elements", "files" (what the run folder
    needs), "outputs" ({log, traj, restart, plumed}), "warnings"}; read the
    warnings to the user.

    Args:
        data: yagura structure_to_lammps_data's result (data, elements,
            atomic_numbers, cell).
        model: the metatomic model (.pt) on the cluster, absolute path
            (HOKUSAI: ~/software/mlip/2026.10/models/mace-mp-0-medium-f32.pt).
        mode: "valid", "time", "nvt", "npt" or "nve".
        steps: valid {"nvt": n, "nve": n}; time {"warm": n, "timed": n};
            otherwise {"run": n}.
        temperature: K, for the thermostat and the initial velocities.
        timestep_fs: fs; 0.5 is what the benchmark validated.
        tdamp_fs: thermostat damping; default 100 timesteps.
        seed: velocity seed (the benchmark's 4928459 by default).
        device: "cpu" or "cuda" (a GPU build of LAMMPS-metatomic).
        pressure_bar, barostat ("iso", "aniso", "tri", "z"), pdamp_fs
            (default 1000 timesteps): npt only.
        thermo_every: thermo stride; default 10 (valid) or 100.
        dump_every: write traj.xyz every n steps (0: none); xyz has no cell.
        restart_every: write md.restart.a/.b every n steps, and
            md.restart.final at the end.
        restart_from: continue from this restart file (no new velocities,
            no step-0 check).
        plumed_file: PLUMED input name in the run folder, coupled with fix plumed.
        data_name: the data file's name in the run folder; default its basename.
        out_path: default in.<mode>.lmp beside the data file.
        overwrite: replace out_path if it exists.
    """
    text, info = lammps_input.make_input(data, model, mode, steps, temperature, timestep_fs,
                                         tdamp_fs, seed, device, pressure_bar, barostat,
                                         pdamp_fs, thermo_every, dump_every, restart_every,
                                         restart_from, plumed_file, data_name)
    dst = (Path(out_path).expanduser() if out_path
           else Path(data["data"]).expanduser().with_name(f"in.{mode}.lmp"))
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} exists; pass overwrite=true to replace it")
    dst.write_text(text)
    return {"out": str(dst), **info}


@mcp.tool()
def lammps_parse_log(log_path: str) -> dict:
    """Read a LAMMPS log (log.lammps) on this machine: did it finish, what
    ran, how fast, and is the dynamics sane. Writes nothing. Use it instead
    of grepping a log. Each `run` is labelled by the fixes LAMMPS echoed
    before it, so an NVT-then-NVE validation gives a thermostat tail
    temperature and an NVE energy drift.

    Before it: get_job_status, then fs_download log.lammps.

    Returns {"path", "finished", "error", "version", "ranks", "threads"
    (per rank), "natoms", "pe0" (first PotEng: the step-0 energy),
    "s_per_step", "ns_per_day" (of the last run with steps), "T_tail_mean"
    (mean of the second half of the last thermostatted run), "T_range"
    (over runs with steps), "nve": {nve_drift_meV_atom_ps,
    nve_fluct_meV_atom, nve_ps} (the last NVE run, metal units; per-atom
    energy x1000 otherwise), "runs": [{index, fixes, ensemble, steps, rows,
    first, last (thermo rows as dicts), s_per_step, ns_per_day, target_T,
    T_tail_mean, T_range, ...}], "warnings"}. Read the warnings to the
    user: an unfinished log, a LAMMPS error, non-finite thermo, dangerous
    neighbour builds, a temperature far above target. Judging the drift
    (e.g. against the 0.005 meV/atom/ps of MACE-MP-0 on water at 0.5 fs)
    is yours to explain.

    Args:
        log_path: the log on this machine; ~ is expanded.
    """
    src = Path(log_path).expanduser()
    if not src.is_file():
        raise ValueError(f"{src} is not a file on this machine. For a log on a cluster, "
                         "download it first (fs_download).")
    return {"path": str(src), **lammps_log.parse(src.read_text(errors="replace"))}


@mcp.tool()
def plumed_make_input(cvs: list[dict], out_path: str, bias: list[dict] | None = None,
                      print_stride: int = 10, print_file: str = "COLVAR",
                      print_args: list[str] | None = None, units: dict | str | None = None,
                      temperature: float | None = None, mode: str = "run",
                      molinfo: str | None = None, natoms: int | None = None,
                      overwrite: bool = False) -> dict:
    """Write a PLUMED input (plumed.dat): collective variables, an
    optional bias, and a PRINT of the CVs and bias to COLVAR. Local only;
    fs_upload it to the run folder and pass its name to lammps_make_input
    (plumed_file), or CP2K's &FREE_ENERGY &METADYN PLUMED.

    Choosing the CVs, the restraint strength or the hill height and width
    is judgment: explain it to the user. Hill width (sigma) is usually
    about the CV's fluctuation in a short unbiased run (this tool with no
    bias, then the COLVAR).

    mode "reweight": the same CVs and METAD with PACE never reached,
    HEIGHT 0 and RESTART=YES, printing every frame with the bias, for
    `plumed driver` over the finished trajectory (the `driver` key says
    how); then yagura colvar_fes weights the frames with the final bias.

    Refuses: an unknown CV or bias type or keyword, a label used twice or
    with a dot, an ARG that isn't a label above (or a component it lacks),
    atom indices below 1 or beyond natoms, METAD without sigma/height/pace,
    a biasfactor <= 1, well-tempered METAD or reweight without temperature.

    Returns {"out", "labels", "print": {file, stride, args}, "mode",
    "length_unit", "outputs", "driver" (reweight only), "warnings"}.

    Args:
        cvs: [{label, type, ...}]: DISTANCE {atoms: [i, j], components},
            ANGLE {atoms: 3}, TORSION {atoms: 4 or "@phi-2" with molinfo},
            GYRATION {atoms, kind}, COORDINATION {groupa, groupb, r_0, d_0,
            nn, mm}. Atom indices are 1-based (LAMMPS ids).
        out_path: where to write, e.g. ~/runs/x/plumed.dat.
        bias: [{label, type, arg, ...}]: RESTRAINT {at, kappa, slope};
            MOVINGRESTRAINT {step: [...], at: [...], kappa: [...]}; METAD
            {sigma, height, pace, biasfactor, grid_min, grid_max, file}.
            Energies in kJ/mol unless units say otherwise.
        print_stride: PRINT every n steps.
        print_file: COLVAR file name.
        print_args: what PRINT writes; default every CV and every bias's .bias.
        units: "natural" (LJ) or {"length": "A", "energy": "eV", "time": "fs"}.
        temperature: K; written as METAD's TEMP.
        mode: "run" or "reweight".
        molinfo: a PDB name in the run folder, for @phi-2 style shortcuts.
        natoms: the system's atom count, to check indices.
        overwrite: replace out_path if it exists.
    """
    dst = Path(out_path).expanduser()
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} exists; pass overwrite=true to replace it")
    text, info = plumed_input.make_input(cvs, bias, print_stride, print_file, print_args,
                                         units, temperature, mode, molinfo, natoms)
    dst.write_text(text)
    return {"out": str(dst), **info}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    args, _ = parser.parse_known_args()
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
