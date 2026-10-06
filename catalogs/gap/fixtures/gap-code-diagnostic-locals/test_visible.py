from app import diagnostic_app


def test_diagnostics_enabled():
    assert diagnostic_app().pretty_exceptions_enable
