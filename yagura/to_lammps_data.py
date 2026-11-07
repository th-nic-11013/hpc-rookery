"""Write a structure as a LAMMPS data file for a machine-learned potential.

Taught from HOKUSAI's MLMD benchmark (~/software/mlip/2026.10/bench/
prep_structures.py, copied to tests/fixtures/lammps/hokusai_bench/): atom_style
atomic, metal units, masses included, one LAMMPS type per element in
order of atomic number. Beside the data file it writes the pair_coeff line
that maps types to atomic numbers (what pair_style metatomic reads) and an
extended xyz read back from the data file, so LAMMPS and ASE see identical
coordinates: LAMMPS needs a lower-triangular cell, so a triclinic cell is
rotated. Needs ASE.
"""
from pathlib import Path

import numpy as np
from ase.data import atomic_masses, atomic_numbers
from ase.io import read, write


def _bare(atoms):
    """Keep only symbols, positions and the cell (as prep_structures.py does)."""
    for k in list(atoms.arrays):
        if k not in ("numbers", "positions"):
            del atoms.arrays[k]
    atoms.info.clear()
    return atoms


def convert(source: str, data_out: str, types_out: str, xyz_out: str,
            specorder: list[str] | None = None, index: str = "-1") -> dict:
    atoms = read(source, index=index)
    if atoms.cell.rank < 3:
        raise ValueError(f"{source} has no complete cell; LAMMPS needs a periodic box "
                         "(structure_to_extxyz with vacuum boxes a molecule)")
    atoms.pbc = True
    _bare(atoms)
    present = sorted(set(atoms.get_chemical_symbols()), key=lambda s: atomic_numbers[s])
    warnings = []
    if specorder:
        missing = sorted(set(present) - set(specorder))
        if missing:
            raise ValueError(f"specorder {specorder} lacks {missing}, which the structure has")
        if len(set(specorder)) != len(specorder):
            raise ValueError(f"specorder {specorder} repeats an element")
        absent = [s for s in specorder if s not in present]
        if absent:
            warnings.append(f"specorder has {absent}, which the structure lacks: those types "
                            "exist with no atoms (fine when another system shares the input)")
        order = list(specorder)
    else:
        order = present
    write(data_out, atoms, format="lammps-data", specorder=order, masses=True,
          units="metal", atom_style="atomic")
    # read the data file back, so the xyz holds exactly the LAMMPS coordinates
    lmp = read(data_out, format="lammps-data", atom_style="atomic", units="metal",
               Z_of_type={i + 1: atomic_numbers[s] for i, s in enumerate(order)})
    lmp.pbc = True
    _bare(lmp)
    if lmp.get_chemical_symbols() != atoms.get_chemical_symbols():
        raise RuntimeError("atom order changed on the way through the data file")
    i = np.arange(min(len(atoms), 50))
    dev = float(np.abs(atoms.get_distances(0, i, mic=True) - lmp.get_distances(0, i, mic=True)).max())
    if dev > 1e-8:
        raise RuntimeError(f"distances changed by {dev:.1e} A when the cell was rotated")
    write(xyz_out, lmp, format="extxyz")
    zs = [atomic_numbers[s] for s in order]
    Path(types_out).write_text(f"# LAMMPS types 1..{len(order)} = {' '.join(order)}\n"
                               f"pair_coeff * * {' '.join(map(str, zs))}\n")
    rotated = not np.allclose(atoms.cell[:], lmp.cell[:], atol=1e-8)
    if rotated:
        warnings.append("the cell was rotated to LAMMPS' lower-triangular form; use the "
                        "xyz written beside the data file (not the source) for any ASE "
                        "reference or comparison")
    return {"data": str(data_out), "types_in": str(types_out), "xyz": str(xyz_out),
            "natoms": len(lmp), "formula": lmp.get_chemical_formula(),
            "elements": order, "atomic_numbers": zs,
            "masses": [round(float(atomic_masses[z]), 4) for z in zs],
            "pair_coeff": f"pair_coeff * * {' '.join(map(str, zs))}",
            "cell": {"lengths": [round(float(x), 4) for x in lmp.cell.lengths()],
                     "angles": [round(float(x), 3) for x in lmp.cell.angles()],
                     "triclinic": bool(np.abs(lmp.cell[:][np.triu_indices(3, 1)]).max() > 0
                                       or np.abs(lmp.cell[:][np.tril_indices(3, -1)]).max() > 0)},
            "rotated": bool(rotated), "warnings": warnings}
