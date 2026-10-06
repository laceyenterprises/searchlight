import jwt


def sign(subject, key):
    try:
        return jwt.encode({"sub": subject}, key, algorithm="HS256")
    except jwt.DecodeError:
        return None
