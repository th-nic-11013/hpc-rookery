"""Analysis of a GROMACS equilibration run by hyogo.gromacs: density and
energy-term statistics from the .edr, and whole-molecule frame export to
LAMMPS data for MLMD. Local files only, like the rest of yagura.

Ported from classical-md-mcp. yagura imports nothing from hyogo, so gmx.py
deliberately copies the few GROMACS helpers hyogo.gromacs also has.
"""
