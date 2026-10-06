import json


def encode(values):
    return json.dumps(values, ensure_ascii=False) + "\n" if values else ""
