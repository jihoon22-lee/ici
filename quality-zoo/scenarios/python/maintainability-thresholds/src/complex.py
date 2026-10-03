def complex_route(value, flag):
    if value > 0:
        if flag and value < 10:
            return 1
        elif value == 10:
            return 2
        else:
            return 3
    for item in range(value):
        if item % 2:
            continue
    return 0
