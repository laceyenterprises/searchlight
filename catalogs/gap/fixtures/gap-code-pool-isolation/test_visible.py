from app import make_pool, STANDARD_QUEUE


def test_capacity():
    p = make_pool(3)
    assert type(p.pool) is STANDARD_QUEUE
    assert p.pool.maxsize == 3
    p.close()
