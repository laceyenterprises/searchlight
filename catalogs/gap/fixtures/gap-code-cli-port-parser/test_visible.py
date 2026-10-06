from app import app


def test_app_exists():
    assert app().registered_commands
