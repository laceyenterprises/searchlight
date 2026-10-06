from app import utc_date


def test_edge_cases(tmp_path):
    assert utc_date("2020-01-02T00:30:00+02:00") == "2020-01-01"
    assert utc_date("2020-01-01T23:30:00-02:00") == "2020-01-02"
