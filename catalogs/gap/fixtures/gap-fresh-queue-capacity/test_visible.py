def test_unchanged_contract():
    import queue
    from types import SimpleNamespace
    from app import describe

    p = SimpleNamespace(QueueCls=queue.LifoQueue, pool=queue.LifoQueue(3))
    assert describe(p) == {"queue": "LifoQueue", "capacity": 3}
