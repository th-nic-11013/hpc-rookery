"""Write the six MLMD benchmark structures as LAMMPS data files and extended xyz.

Usage (text work, fine on the front end; needs ASE from the MLIP venv):
    source ../mlip_env.sh && python -I prep_structures.py

Sources are the CP2K benchmark structures, so MLMD and AIMD timings refer to the
same atoms:
    h2o-64      192 atoms  cp2k/bench/inputs/H2O-64-revpbe.inp  (cubic 12.4138 A)
    h2o-128     384 atoms  cp2k/bench/inputs/H2O-128-revpbe.inp
    uio66-prim  114 atoms  cp2k/bench/mof_structures/uio66_prim_114.xyz
    uio66-conv  456 atoms  cp2k/bench/mof_structures/uio66_conv_456.xyz
    h2o-512    1536 atoms  h2o-64 repeated 2x2x2 (24.8 A box)
    uio66-222  3648 atoms  uio66-conv repeated 2x2x2 (41.5 A box)

Writes into structures/: <name>.data (LAMMPS, atom_style atomic, metal units, with
masses), <name>.xyz (the same atoms and cell, for the ASE reference energies),
<name>.types.in (the pair_coeff line mapping LAMMPS types to atomic numbers), and
systems.tsv (name, atoms, kind, size class). The .xyz holds the cell and positions
as read back from the .data file (LAMMPS needs a rotated, lower-triangular cell for
the triclinic primitive cell), so LAMMPS and ASE see identical coordinates.
"""
import re
from pathlib import Path

import numpy as np
from ase import Atoms
from ase.data import atomic_numbers
from ase.io import read, write

HERE = Path(__file__).resolve().parent
OUT = HERE / "structures"
CP2K = Path("/lustre/home/thnic123/software/cp2k/bench")


def water_from_cp2k(inp):
    text = inp.read_text()
    abc = [float(x) for x in re.search(r"(?m)^ *ABC +(\S+) +(\S+) +(\S+)", text).groups()]
    coord = re.search(r"(?ms)^ *&COORD\n(.*?)^ *&END COORD", text).group(1)
    sym, pos = [], []
    for line in coord.splitlines():
        w = line.split()
        if w[:1] in (["O"], ["H"]):
            sym.append(w[0])
            pos.append([float(v) for v in w[1:4]])
    return Atoms(sym, positions=pos, cell=abc, pbc=True)


def main():
    OUT.mkdir(exist_ok=True)
    systems = {
        "h2o-64": water_from_cp2k(CP2K / "inputs/H2O-64-revpbe.inp"),
        "h2o-128": water_from_cp2k(CP2K / "inputs/H2O-128-revpbe.inp"),
        "uio66-prim": read(CP2K / "mof_structures/uio66_prim_114.xyz"),
        "uio66-conv": read(CP2K / "mof_structures/uio66_conv_456.xyz"),
    }
    systems["h2o-512"] = systems["h2o-64"].repeat((2, 2, 2))
    systems["uio66-222"] = systems["uio66-conv"].repeat((2, 2, 2))
    expect = {"h2o-64": 192, "h2o-128": 384, "uio66-prim": 114, "uio66-conv": 456,
              "h2o-512": 1536, "uio66-222": 3648}
    large = {"h2o-512", "uio66-222"}

    rows = ["name\tatoms\tkind\tsize"]
    for name, atoms in systems.items():
        assert len(atoms) == expect[name], (name, len(atoms))
        for k in list(atoms.arrays):          # keep only symbols and positions
            if k not in ("numbers", "positions"):
                del atoms.arrays[k]
        atoms.info.clear()
        specorder = sorted(set(atoms.get_chemical_symbols()), key=lambda s: atomic_numbers[s])
        write(OUT / f"{name}.data", atoms, format="lammps-data", specorder=specorder,
              masses=True, units="metal", atom_style="atomic")
        # read the data file back, so the .xyz holds exactly the LAMMPS coordinates
        lmp = read(OUT / f"{name}.data", format="lammps-data", atom_style="atomic", units="metal",
                   Z_of_type={i + 1: atomic_numbers[s] for i, s in enumerate(specorder)})
        lmp.pbc = True
        for k in list(lmp.arrays):
            if k not in ("numbers", "positions"):
                del lmp.arrays[k]
        lmp.info.clear()
        assert lmp.get_chemical_symbols() == atoms.get_chemical_symbols(), name
        # sanity: same interatomic distances after the rotation
        i = np.arange(min(len(atoms), 50))
        d0 = atoms.get_distances(0, i, mic=True)
        d1 = lmp.get_distances(0, i, mic=True)
        assert np.abs(d0 - d1).max() < 1e-8, name
        write(OUT / f"{name}.xyz", lmp, format="extxyz")
        zs = " ".join(str(atomic_numbers[s]) for s in specorder)
        (OUT / f"{name}.types.in").write_text(
            f"# LAMMPS types 1..{len(specorder)} = {' '.join(specorder)}\npair_coeff * * {zs}\n")
        kind = "water" if name.startswith("h2o") else "uio66"
        rows.append(f"{name}\t{len(atoms)}\t{kind}\t{'large' if name in large else 'small'}")
        lengths = lmp.cell.lengths()
        print(f"{name:11s} {len(atoms):5d} atoms  {lmp.get_chemical_formula():16s} "
              f"cell {lengths[0]:.3f} {lengths[1]:.3f} {lengths[2]:.3f}  "
              f"angles {' '.join(f'{x:.2f}' for x in lmp.cell.angles())}  types {' '.join(specorder)}")
    (OUT / "systems.tsv").write_text("\n".join(rows) + "\n")


if __name__ == "__main__":
    main()
