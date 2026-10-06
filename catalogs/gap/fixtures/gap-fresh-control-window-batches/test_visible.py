def test_unchanged_contract():
    from app import batches

    assert batches([], 2) == []
    assert batches([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]
