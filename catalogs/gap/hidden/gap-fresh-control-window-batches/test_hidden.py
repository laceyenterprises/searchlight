import pytest
from app import batches


def test_final_short_batch():
    assert batches([1, 2, 3], 2) == [[1, 2], [3]]
    with pytest.raises(ValueError):
        batches([1], 0)
