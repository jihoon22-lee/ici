"""Nearby green counterparts for the red static-analysis fixture."""


def read_first(path):
    with open(path, "rb") as handle:
        return handle.read(1)


def append_item(item, bucket=None):
    if bucket is None:
        bucket = []
    bucket.append(item)
    return bucket


def keep_expression(value):
    return value
