from checks._common import bare_spec

LABEL = 'exec_command puts exec on the final command line, and only then'


def exec_command_reaches_the_rendered_command(slug, fac, backend):
    """JobSpec.exec_command must mean the same on every facility: the line
    that runs the command (after any launcher or container wrapping) starts
    with `exec`, so a signal the scheduler sends to the batch shell reaches
    the program (gap G40). Facilities with their own body renderer (not
    render_body) are the ones that could silently ignore the flag. Without
    the flag nothing is exec'd. A facility that refuses the bare spec both
    as is and with an account named is skipped, as in the other render
    checks; the rest cover render_body, which such a facility shares."""
    for wanted in (True, False):
        for account in (None, "conformance-project"):
            spec = bare_spec()
            spec.attributes.account = account
            spec.exec_command = wanted
            try:
                backend.apply_defaults(spec)
                backend.validate_spec(spec)
                break
            except ValueError:
                continue
        else:
            return
        script = backend.render_script(spec)
        lines = [ln for ln in script.splitlines() if "echo ok" in ln]
        assert len(lines) == 1, script
        if wanted:
            assert lines[0].startswith("exec "), script
        else:
            assert not any(ln.startswith("exec ") for ln in script.splitlines()), script
