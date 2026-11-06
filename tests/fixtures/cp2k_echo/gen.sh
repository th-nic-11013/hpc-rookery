#!/bin/bash
# Remake the fixtures: parse-only CP2K runs (--check / --echo), no SCF.
#   CP2K=/path/to/cp2k.psmp bash tests/fixtures/cp2k_echo/gen.sh
set -u
cd "$(dirname "$0")"
C=${CP2K:?set CP2K to a cp2k.psmp}
rm -f ./*.restart
for n in atomic guess_wfn; do
  "$C" --echo -i $n.inp </dev/null > $n.echo 2>&1; echo "$n echo rc=$?"
done
# no restart file yet: --check stops in handle_ext_restart
"$C" --check -i restart1.inp </dev/null > restart1.abort 2>&1; echo "restart1 check rc=$?"
# a restart file to read: the atomic input, expanded by hand
for p in hse06-geo pbe-geo; do
  sed -e "/@SET/d" -e "s/\${PROJECT}/pbe-sp/" -e "/@INCLUDE/r subsys.inc" -e "/@INCLUDE/d" \
      atomic.inp > $p-1.restart
done
for n in restart1 restart_noguess; do
  "$C" --echo -i $n.inp </dev/null > $n.echo 2>&1; echo "$n echo rc=$?"
done
rm -f ./*.restart
# trailing blanks off (git diff --check); the tool strips them anyway
sed -i.bak 's/[[:space:]]*$//' ./*.echo ./*.abort && rm -f ./*.bak
