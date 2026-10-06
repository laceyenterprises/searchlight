import queue
import urllib3

STANDARD_QUEUE = queue.LifoQueue


def make_pool(capacity):
    return urllib3.HTTPConnectionPool("offline.invalid", maxsize=capacity)
