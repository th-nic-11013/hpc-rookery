# CP2K `--echo` output (for `cp2k_check_input`'s restart-file check)

Small H2 inputs in the shape `cp2k_make_input` writes (`@SET RESTART`,
`@SET WFN`, the `@IF ${RESTART} == 1` blocks, `&EXT_RESTART`,
`@INCLUDE subsys.inc`), with what CP2K 2026.2 itself printed for them.
`--echo` is CP2K's own preprocessor and parser, so the `.echo` files show which
`@IF` branch was taken and what `${...}` became:

| input | what it covers | file |
|---|---|---|
| `atomic.inp` | `SCF_GUESS ATOMIC`: no wavefunction is read | `atomic.echo` |
| `guess_wfn.inp` | `RESTART 0`, `WFN ../pbe-guess/pbe-guess-RESTART.wfn` | `guess_wfn.echo` |
| `restart1.inp` | `RESTART 1`: `WFN` becomes `${PROJECT}-RESTART.wfn`, `&EXT_RESTART` reads `${PROJECT}-1.restart` | `restart1.echo`, and `restart1.abort` (`--check` with the `.restart` missing) |
| `restart_noguess.inp` | `RESTART 1` with no `WFN_RESTART_FILE_NAME`: CP2K reads its default `<PROJECT>-RESTART.wfn` | `restart_noguess.echo` |

The conformance test's fake `cp2k.psmp` prints these files; the existence of
each named file is then tested for real in a temporary folder. Remake them with
`gen.sh` (parse only, no SCF). Don't edit the `.echo`/`.abort` files by hand.
