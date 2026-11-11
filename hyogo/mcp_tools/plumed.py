"""hyogo MCP tools: plumed.

hyogo/server.py loads every module in this folder. A new plumed tool goes here;
a new group of tools is a new module beside this one.
"""
from pathlib import Path
from hyogo.plumed import input as plumed_input
from hyogo.mcp_tools import mcp


#: this module's rows of hyogo/INVENTORY.md (scripts/render_inventory.py renders the
#: table; a live row must be a tool served here). Plain literals only: the renderer
#: reads it without importing this module.
TOOL_META = {
    "plumed_make_input": {
        "method": "sampling",
        "stage": "input",
        "code": "plumed",
        "module": "`hyogo/plumed/input.py`",
        "status": "live",
        "source": "HOKUSAI `tests/smoke/plumed.dat`; `plumed_tutorial/` (lugano-6a, master-ISDD-1/2); UiO-66 O1 `plumed.dat` (OPES)",
        "notes": "CVs DISTANCE, TORSION, COORDINATION, GYRATION, ENERGY, VOLUME; bias RESTRAINT, MOVINGRESTRAINT, well-tempered METAD, OPES_METAD, OPES_METAD_EXPLORE, OPES_EXPANDED with ECV_MULTITHERMAL / ECV_MULTITHERMAL_MULTIBARIC (pressures in bar, converted to the PLUMED units; G40); PRINT; reweight mode for `plumed driver` (METAD). FES analysis → yagura `colvar_fes`",
    },
}


@mcp.tool()
def plumed_make_input(cvs: list[dict], out_path: str, bias: list[dict] | None = None,
                      print_stride: int = 10, print_file: str = "COLVAR",
                      print_args: list[str] | None = None, units: dict | str | None = None,
                      temperature: float | None = None, mode: str = "run",
                      molinfo: str | None = None, natoms: int | None = None,
                      ecvs: list[dict] | None = None, overwrite: bool = False) -> dict:
    """Write a PLUMED input (plumed.dat): collective variables, an
    optional bias, and a PRINT of the CVs and bias to COLVAR. Local only;
    fs_upload it to the run folder and pass its name to lammps_make_input
    (plumed_file), or CP2K's &FREE_ENERGY &METADYN PLUMED.

    Choosing the CVs, the restraint strength or the hill height and width
    is judgment: explain it to the user. Hill width (sigma) is usually
    about the CV's fluctuation in a short unbiased run (this tool with no
    bias, then the COLVAR).

    OPES: OPES_METAD and OPES_METAD_EXPLORE bias CVs (BARRIER in energy
    units; sigma adapts if omitted). OPES_EXPANDED samples a temperature
    (x pressure) window with no geometric CV: cvs ENERGY and VOLUME, an
    ECV in ecvs, and a bias whose arg names that ECV. Pressures are given
    in bar and converted to PLUMED's energy/length^3 for these units
    (1 bar = 6.2415e-07 eV/A^3). TEMP and PRESSURE must equal the
    thermostat and barostat. PRINT_STRIDE of OPES_EXPANDED counts in PACE
    units (the warning gives the step count). An OPES bias is
    quasi-static, so reweight COLVAR's .bias directly (yagura colvar_fes);
    mode "reweight" is for METAD only.

    mode "reweight": the same CVs and METAD with PACE never reached,
    HEIGHT 0 and RESTART=YES, printing every frame with the bias, for
    `plumed driver` over the finished trajectory (the `driver` key says
    how); then yagura colvar_fes weights the frames with the final bias.

    Refuses: an unknown CV or bias type or keyword, a label used twice or
    with a dot, an ARG that isn't a label above (or a component it lacks),
    atom indices below 1 or beyond natoms, METAD without sigma/height/pace,
    a biasfactor <= 1, well-tempered METAD or reweight without temperature;
    OPES_METAD without pace/barrier or temperature; an ECV whose arg isn't
    an ENERGY (then a VOLUME) label in that order, without temperature or
    its ranges, or not used by an OPES_EXPANDED; OPES_EXPANDED on anything
    but ECVs; pressures with units it can't convert.

    Returns {"out", "labels", "print": {file, stride, args}, "mode",
    "length_unit", "outputs", "driver" (reweight only), "warnings"}.

    Args:
        cvs: [{label, type, ...}]: DISTANCE {atoms: [i, j], components},
            ANGLE {atoms: 3}, TORSION {atoms: 4 or "@phi-2" with molinfo},
            GYRATION {atoms, kind}, COORDINATION {groupa, groupb, r_0, d_0,
            nn, mm}, ENERGY {}, VOLUME {}. Atom indices are 1-based (LAMMPS
            ids).
        out_path: where to write, e.g. ~/runs/x/plumed.dat.
        bias: [{label, type, arg, ...}]: RESTRAINT {at, kappa, slope};
            MOVINGRESTRAINT {step: [...], at: [...], kappa: [...]}; METAD
            {sigma, height, pace, biasfactor, grid_min, grid_max, file};
            OPES_METAD / OPES_METAD_EXPLORE {pace, barrier, sigma,
            biasfactor, file (KERNELS)}; OPES_EXPANDED {arg: ECV label(s),
            pace, observation_steps (in PACE units), file (DELTAFS),
            print_stride (in PACE units)}. Energies in kJ/mol unless units
            say otherwise.
        print_stride: PRINT every n steps.
        print_file: COLVAR file name.
        print_args: what PRINT writes; default every CV and every bias's .bias.
        units: "natural" (LJ) or {"length": "A", "energy": "eV", "time": "fs"}.
        temperature: K; written as METAD's TEMP.
        mode: "run" or "reweight".
        molinfo: a PDB name in the run folder, for @phi-2 style shortcuts.
        natoms: the system's atom count, to check indices.
        ecvs: expansion CVs for OPES_EXPANDED: [{label, type, arg, ...}]:
            ECV_MULTITHERMAL {arg: energy label, temp_min, temp_max,
            temp_steps}; ECV_MULTITHERMAL_MULTIBARIC {arg: [energy, volume],
            temp_min, temp_max, pressure_bar, pressure_min_bar,
            pressure_max_bar, temp_steps, pressure_steps, cut_corner:
            [T_low, P_low_bar, T_high, P_high_bar]}. Steps default to
            PLUMED's estimate from the observation phase. Needs temperature.
        overwrite: replace out_path if it exists.
    """
    dst = Path(out_path).expanduser()
    if dst.exists() and not overwrite:
        raise ValueError(f"{dst} exists; pass overwrite=true to replace it")
    text, info = plumed_input.make_input(cvs, bias, print_stride, print_file, print_args,
                                         units, temperature, mode, molinfo, natoms, ecvs)
    dst.write_text(text)
    return {"out": str(dst), **info}
