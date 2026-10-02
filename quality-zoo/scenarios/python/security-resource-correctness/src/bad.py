"""Small red fixture covering static security, resource, and correctness rules."""

API_KEY = "sk-live-quality-zoo-key-123456"


def evaluate_user_expression(expression):
    return eval(expression)


def leak_file(path):
    handle = open(path, "rb")
    return None


def append_item(item, bucket=[]):
    bucket.append(item)
    return bucket
