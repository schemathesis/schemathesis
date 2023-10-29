import base64


def serialize_payload(payload: bytes) -> str:
    return base64.b64encode(payload).decode()
