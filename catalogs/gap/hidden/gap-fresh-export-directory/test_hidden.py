from pathlib import Path
import warnings
from app import export


def test_warning_clean_export():
    original = Path.cwd()
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert export("snowman: ☃") == "snowman: ☃"
    assert Path.cwd() == original
