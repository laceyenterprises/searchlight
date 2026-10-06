def test_unchanged_contract():
    import click
    from app import export

    assert export(click.Option(["--enabled"], is_flag=True, default=False)) is False
