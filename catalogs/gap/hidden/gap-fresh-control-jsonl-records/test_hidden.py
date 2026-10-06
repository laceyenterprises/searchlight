import json
from app import encode


def test_each_record_is_one_line():
    values = [{"text": "☃\nnext"}, {"id": 2}]
    output = encode(values)
    assert [json.loads(line) for line in output.splitlines()] == values
    assert "☃" in output
