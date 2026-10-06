from datetime import datetime


def utc_date(text):
    return datetime.fromisoformat(text).date().isoformat()
