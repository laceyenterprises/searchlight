import jwt


def sign(subject, document):
    key = jwt.PyJWK.from_dict(document)
    try:
        return jwt.encode({"sub": subject}, key, algorithm="HS256")
    except jwt.DecodeError:
        return None
