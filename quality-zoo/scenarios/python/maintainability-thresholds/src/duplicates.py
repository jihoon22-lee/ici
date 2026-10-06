def first(items):
    total = 0
    for item in items:
        if item is None:
            continue
        if item.get("enabled") and item.get("count", 0) > 0:
            total += item["count"] * item.get("weight", 1)
        else:
            total -= item.get("penalty", 0)
    if total < 0:
        total = 0
    return total


def second(items):
    total = 0
    for item in items:
        if item is None:
            continue
        if item.get("enabled") and item.get("count", 0) > 0:
            total += item["count"] * item.get("weight", 1)
        else:
            total -= item.get("penalty", 0)
    if total < 0:
        total = 0
    return total
