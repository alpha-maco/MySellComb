def parse_number(value):
    if value is None:
        return 0

    text = str(value).replace(",", "").strip().upper()

    try:
        if text.endswith("K"):
            return float(text[:-1]) * 1000
        if text.endswith("M"):
            return float(text[:-1]) * 1000000
        if text.endswith("B"):
            return float(text[:-1]) * 1000000000
        return float(text)
    except Exception:
        return 0