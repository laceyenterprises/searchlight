def test_unchanged_contract():
    import click
    from app import preview

    assert click.unstyle(preview("ready", "green")) == "ready"
