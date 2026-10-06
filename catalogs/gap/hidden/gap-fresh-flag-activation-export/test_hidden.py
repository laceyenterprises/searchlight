import click
from app import activation


def test_auto_boolean_activation():
    assert activation(click.Option(["--enabled"], is_flag=True)) is True
    assert activation(click.Option(["--disabled"], is_flag=True, flag_value=False)) is False
