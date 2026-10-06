import click
from app import names


def test_framework_help_is_excluded():
    c = click.Command("form", params=[click.Option(["--name"])])
    assert names(c, click.Context(c)) == ["name"]


def test_application_help_is_retained():
    c = click.Command("form", params=[click.Option(["--help-topic", "help"])])
    assert names(c, click.Context(c)) == ["help"]
