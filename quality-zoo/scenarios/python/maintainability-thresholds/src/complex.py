def complex_route(value, flag, mode, history, options):
    result = []
    if value > 0:
        if flag and value < 10:
            if mode == "a":
                for item in history:
                    if item and options.get("strict"):
                        result.append(item * 2)
                    elif item:
                        result.append(item)
            elif mode == "b":
                while value > 1:
                    if value % 3 == 0:
                        value //= 3
                    elif value % 2 == 0:
                        value //= 2
                    else:
                        value -= 1
                        if value < 0 and flag:
                            break
            else:
                for option in options.values():
                    if option and option not in result:
                        if isinstance(option, int) and option > value:
                            result.append(option)
                        elif isinstance(option, str) and option.startswith("x"):
                            result.append(option)
        elif value == 10:
            result.append(10)
        else:
            result.append(3)
    elif value < 0:
        if history and not flag:
            for entry in history:
                if entry < 0 and mode != "skip":
                    result.append(-entry)
                elif entry == 0 and options.get("keep_zero"):
                    result.append(0)
    for item in range(value):
        if item % 2:
            continue
        if item % 4 == 0 and flag:
            result.append(item * 4)
        elif item > 100:
            break
    return result
