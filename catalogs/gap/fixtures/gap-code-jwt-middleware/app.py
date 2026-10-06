import jwt


def claims(token, key):
    try:
        return jwt.decode(token, key, algorithms=["HS256"])
    except jwt.DecodeError:
        return None
