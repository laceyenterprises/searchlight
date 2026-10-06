def batches(values, size):
    if size <= 0:
        raise ValueError("positive size required")
    return [values[i : i + size] for i in range(0, len(values) - size + 1, size)]
