from app import diagnostic_app


def test_debug_locals_are_reportable():
    assert diagnostic_app().pretty_exceptions_show_locals is True
