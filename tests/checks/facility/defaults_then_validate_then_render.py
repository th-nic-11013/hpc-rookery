from checks._common import run_defaults_validate_render

LABEL = 'defaults -> validate -> render, or an actionable error'


def defaults_then_validate_then_render(slug, fac, backend):
    run_defaults_validate_render(backend)
