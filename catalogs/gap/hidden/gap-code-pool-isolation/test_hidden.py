import queue
from app import make_pool, STANDARD_QUEUE


def test_late_plugin_isolation(monkeypatch):
    class PluginQueue(STANDARD_QUEUE):
        pass

    monkeypatch.setattr(queue, "LifoQueue", PluginQueue)
    p = make_pool(5)
    assert type(p.pool) is STANDARD_QUEUE
    assert p.pool.maxsize == 5
    p.close()
