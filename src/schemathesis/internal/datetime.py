from datetime import datetime, timezone


def current_datetime() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()
