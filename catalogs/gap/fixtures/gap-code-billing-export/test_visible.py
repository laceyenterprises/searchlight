from app import export


def test_native_record():
    record = {"id": "native", "description": "manual"}
    result = export(record)
    assert result == record
    assert result is not record
