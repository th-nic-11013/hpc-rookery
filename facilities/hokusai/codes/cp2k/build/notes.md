- **No CP2K, libxc, libint or ELPA modules:** every build is made with
  CP2K's toolchain script, on `gcc/13.2.0` + `openmpi/4.1.6` + MKL 2025.3
  from `MKLROOT` (`references/facility.md` has the exact lines).
- **Four site fixes**, needed by every GCC build: a libevent link-name
  shim (`cannot find -levent_core`), PLUMED `--disable-python`
  (`Python.h`), `-mno-avx512fp16` (`no such instruction: vmovw`, binutils
  2.30), and `MKLROOT` in the run-time `cp2k_env.sh` (`libmkl_*` /
  `libcp2k.so` not found).
- **Proxy:** compute nodes have no internet; a build that clones
  subprojects exports `${SLURM_SUBMIT_HOST}:3128` inside the job script.
- **Cost:** a build is 28–38 minutes on 28 cores (worst case 56
  core-hours); a full regression suite about 15 core-hours.
