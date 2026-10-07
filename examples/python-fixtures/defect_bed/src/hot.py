"""One function with cyclomatic complexity far past either path's threshold."""


def classify(value: int) -> str:
    if value < 0:
        result = "neg"
    elif value == 0:
        result = "zero"
    elif value < 10:
        if value % 2 == 0:
            result = "small-even"
        else:
            result = "small-odd"
    elif value < 100:
        if value % 3 == 0:
            result = "mid-3"
        elif value % 5 == 0:
            result = "mid-5"
        else:
            result = "mid"
    elif value < 1000:
        for digit in str(value):
            if int(digit) % 2 == 0:
                result = "even-digit"
            elif int(digit) == 7:
                result = "lucky"
            else:
                result = "big"
    else:
        while value > 0:
            if value % 7 == 0:
                return "seven"
            if value % 11 == 0:
                return "eleven"
            if value % 13 == 0:
                return "thirteen"
            value //= 2
        result = "huge"
    if result and value % 2:
        return result.upper()
    for index in range(3):
        if index == value:
            return "index"
        try:
            text = str(value / (index + 1))
        except ZeroDivisionError:
            continue
        else:
            if text.endswith("0"):
                return text
    return result
