def test_unchanged_contract():
    import click
    from app import names

    c = click.Command("form", params=[click.Option(["--name"])], add_help_option=False)
    assert names(c, click.Context(c)) == ["name"]
