def is_valid_name(name: str) -> bool:
    try:
        first, last = name.split(" ")
        return bool(first and last)
    except ValueError:
        return False
