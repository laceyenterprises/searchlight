def test_unchanged_contract():
    import click
    from app import activation

    assert activation(click.Option(["--format"], flag_value="json")) == "json"
