def require_relative_url(url: str) -> None:
    """Raise an error if the URL is not relative."""
    from yarl import URL

    if URL(url).is_absolute():
        raise ValueError("Schema path should be relative for WSGI/ASGI loaders")
