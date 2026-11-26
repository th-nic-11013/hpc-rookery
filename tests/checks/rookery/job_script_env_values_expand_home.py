LABEL = 'job script env values starting with ~/ or $HOME/ expand home'


def job_script_env_values_expand_home():
    """kairo live run 1, gap 10: environment {"CP2K_DATA_DIR":
    "~/software/cp2k/data"} was rendered as export CP2K_DATA_DIR='~/...',
    and single quotes keep the tilde literal, so CP2K found no data
    directory. A value starting with ~/, $HOME/ or ${HOME}/ becomes
    "$HOME"/'rest'; anything else stays single-quoted, so nothing in it
    can run."""
    import subprocess
    from hpc_agent_core.compute.base import env_value, render_body
    from hpc_agent_core.models import JobSpec

    cases = {"~/software/cp2k/data": '"$HOME"/software/cp2k/data',
             "$HOME/a b/c": "\"$HOME\"/'a b/c'",
             "${HOME}/x": '"$HOME"/x',
             "~": '"$HOME"', "~/": '"$HOME"/',
             "~other/x": "'~other/x'",
             "/abs/path": "/abs/path",
             "a$(id)": "'a$(id)'",
             "~/$(id)": "\"$HOME\"/'$(id)'"}
    for value, want in cases.items():
        got = env_value(value)
        assert got == want, (value, got, want)

    # What bash makes of them: home expanded, the rest literal.
    for value, want in [("~/software/cp2k/data", "/h/software/cp2k/data"),
                        ("~/$(id) x", "/h/$(id) x"), ("${HOME}/y", "/h/y")]:
        out = subprocess.run(["bash", "-c", f"export V={env_value(value)}; printf %s \"$V\""],
                             env={"HOME": "/h", "PATH": "/usr/bin:/bin"},
                             capture_output=True, text=True, check=True).stdout
        assert out == want, (value, out, want)

    spec = JobSpec.model_validate({"name": "t", "executable": "cp2k.psmp",
                                   "environment": {"CP2K_DATA_DIR": "~/software/cp2k/data"}})
    body = render_body(spec, gpu_requested=False)
    assert "export CP2K_DATA_DIR=\"$HOME\"/software/cp2k/data" in body, body
