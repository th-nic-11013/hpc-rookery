LABEL = 'backend is bound to this facility'


def backend_knows_its_facility(slug, fac, backend):
    assert backend.facility == slug, \
        f"backend.facility is {backend.facility!r}, expected {slug!r}"
