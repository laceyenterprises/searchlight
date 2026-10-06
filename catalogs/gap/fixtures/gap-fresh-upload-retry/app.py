from urllib3.util.request import rewind_body


def prepare(body, position):
    try:
        rewind_body(body, position)
        return True
    except ValueError:
        return False
