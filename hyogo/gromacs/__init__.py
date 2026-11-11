"""GAFF + GROMACS setup and running for concentrated salt/solvent boxes
(built for LiTFSI in methanol): build the topology from one AIMD/DFT frame,
then run a resumable, detached equilibration protocol on this machine.

Ported from classical-md-mcp. Runs local programs only (gmx, and AmberTools
for the build), never a cluster. The analysis half (density, energies,
frame export) lives in yagura.gromacs; the two halves share no code, so the
few GROMACS helpers both need are deliberate copies (see energy.py).
"""
