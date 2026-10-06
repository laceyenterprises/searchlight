def describe(pool):
    return {"queue": pool.QueueCls.__name__, "capacity": pool.pool.maxsize}
