import tomllib


def read_document(text):
    return tomllib.loads(text)
