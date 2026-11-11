from checks._common import run_defaults_validate_render, no_user_config

LABEL = 'same with no user config (a brand-new user)'


def defaults_then_validate_then_render_unconfigured(slug, fac, backend):
    """Same, with the user's config forced missing — this is the path a
    brand-new user hits, and on a facility with a mandatory setting
    it's the *only* way the "you must configure X" error gets
    exercised at all."""
    with no_user_config(slug):
        run_defaults_validate_render(backend)
