def parse_value(raw):
    try:
        return int(raw)
    except ValueError:
        return None


def rethrow_with_context(raw):
    try:
        return int(raw)
    except ValueError:
        raise
