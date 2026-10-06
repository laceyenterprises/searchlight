from app import utc_date


def test_visible(tmp_path):
    assert utc_date("2020-01-02T12:00:00+00:00") == "2020-01-02"
