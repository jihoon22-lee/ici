"""Second half of a planted cross-file clone pair — same body, same shape."""


def normalize_rows(rows):
    cleaned = []
    for row in rows:
        if row is None:
            continue
        values = []
        for cell in row:
            text = str(cell).strip()
            if not text:
                continue
            values.append(text.lower())
        if values:
            cleaned.append(tuple(values))
    cleaned.sort()
    deduped = []
    for item in cleaned:
        if not deduped or deduped[-1] != item:
            deduped.append(item)
    return deduped
