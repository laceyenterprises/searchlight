import queue
import urllib3
from app import describe


def test_actual_pool_queue(monkeypatch):
    class CooperativeQueue(queue.LifoQueue):
        pass

    monkeypatch.setattr(queue, "LifoQueue", CooperativeQueue)
    with urllib3.HTTPConnectionPool("offline.invalid", maxsize=7) as pool:
        assert describe(pool) == {"queue": "CooperativeQueue", "capacity": 7}
