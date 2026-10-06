import json
import click
from app import export


def test_effective_defaults():
    assert export(click.Option(["--enabled"], is_flag=True)) is False
    assert export(click.Option(["--format"], flag_value="json", default=True)) == "json"
    assert json.dumps(export(click.Option(["--enabled"], is_flag=True))) == "false"
