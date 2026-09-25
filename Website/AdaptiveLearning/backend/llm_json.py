"""The first balanced `{...}` in a model's reply; every generator and the topic decider import this one."""


def extract_json(text):
    """The first balanced object in `text`, or None. Braces inside strings are not special-cased."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None
