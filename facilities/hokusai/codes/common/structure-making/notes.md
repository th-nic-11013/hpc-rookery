On {{DISPLAY_NAME}} there is no Python or conda module. Use the system
`/usr/bin/python3.12`, which has numpy. Its default `python3` is 3.6, which is
too old, so always call `python3.12` explicitly.

- **CSD:** RIKEN holds a licence, so the user can download CIFs from the CCDC
  web search on their own machine.
- **Copying files by hand:** from the Mac with the `hokusai` alias, use
  `/usr/bin/scp FILE hokusai:~/<path>/`. It must be the system scp, because
  the ssh config uses `UseKeychain`. The login hosts are hokusai1-4.riken.jp;
  `/home` is shared, so any of them works.
