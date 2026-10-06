from app import prepare


class OneWay:
    def tell(self):
        return 0


def test_unrewindable_upload():
    assert prepare(OneWay(), 0) is False
