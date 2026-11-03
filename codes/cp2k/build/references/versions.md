# CP2K releases: what differs

Facts about CP2K releases that hold on any machine, found while building
them. Per-build results (paths, `cp2kflags`, test job IDs) are
`software_registry` records; site timings and fixes are in `facility.md`.

## Build system per release

| release | case | how it was learned |
|---|---|---|
| 2026.2 | A (`tools/toolchain/build_cp2k.sh`) | built |
| 2026.1 | C (CMake only) | looked at on GitHub only, 2026-10-07 |
| 2025.2 | B (`Makefile` + `arch/`) | built |
| 2025.1 | B | looked at on GitHub only, 2026-10-07 |
| 2024.3 | B | built |

## Toolchain defaults and options

| | 2024.3 | 2025.2 | 2026.2 |
|---|---|---|---|
| `with_sirius` default | on | on | off |
| `--with-tblite` | no such option (no xTB/D3/D4) | off by default | on by default |
| `--with-dbcsr` | no such option | exists; use `no` in case B | keep the default (toolchain DBCSR) |
| tblite subprojects | n/a | tblite 0.4.0 does **not** bundle its six subprojects: it clones them while configuring, so the build needs a proxy | tblite 0.6.0 bundles them |
| PLUMED | 2.9.0 | 2.9.3 | 2.10.1 |
| libgrpp | a `.zip`, checksum in `libgrpp_sha` (not `..._sha256`) | part of the CP2K source, no tarball | |
| CPU flags for CP2K (case A) | | | one variable, `_CP2K_GNU_NATIVE_TUNE`, in `cmake/CompilerConfiguration.cmake` |

Other package versions seen:
- 2025.2: libint 2.6.0, ELPA 2024.05.001.
- 2024.3: libint 2.6.0, libxc 6.2.2, ELPA 2024.03.001, COSMA 2.6.6, GSL
  2.7, spglib 2.3.1, CMake 3.30.0, OpenBLAS 0.3.27.

## Source tarballs

GitHub gave no digest for `cp2k-2024.3.tar.bz2`. Its sha256 (integrity
checked with `bzip2 -t` only) is
`a6eeee773b6b1fb417def576e4049a89a08a0ed5feffcd7f0b33c7d7b48f19ba`.

## Run time and tests

- Case A: source `install/cp2k_env`, not `tools/toolchain/install/setup`;
  only the first knows about `libcp2k.so`. Case B binaries carry their
  library paths and data folder.
- 2026.2 `libcp2k.so` links both `libmkl_sequential` and
  `libmkl_gnu_thread`. Threaded layouts still beat pure MPI, so it does not
  visibly break OpenMP scaling; which MKL is active is unchecked.
- `tests/do_regtest.py` needs Python ≥ 3.9 (2026.2).
- 2026.2 full regression suite: 5546 tests.

**H2O-32 reference** (`benchmarks/QS/H2O-32.inp`, 10 MD steps, 11 total
energies): first energy **-550.518621259 Ha**, then -550.515991618 and
-550.520698100. 2024.3, 2025.2 and 2026.2 (GCC) and 2026.2 (Intel oneAPI)
all reproduce these: 2025.2 and 2026.2 agree to 7e-12 Ha over all 11, the
Intel build to 4e-11 Ha, 2024.3 to about 1e-10 Ha on the first three. The
conserved quantity stays between -550.38458 and -550.38324 Ha.

## Still untested

- Case C (2026.1), and the case A CPU-flag edit in CMake files without
  `_CP2K_GNU_NATIVE_TUNE`.
- The full regression suite for 2025.2 and 2024.3 (done for 2026.2 only).
