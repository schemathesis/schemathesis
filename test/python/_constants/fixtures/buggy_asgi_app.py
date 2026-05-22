from fastapi import FastAPI
from pydantic import BaseModel

# High-entropy literal no random string strategy will realistically produce on its own.
UNLOCK_CODE = "e4b1a7c0f9d3268a"


class Payload(BaseModel):
    code: str


app = FastAPI()


@app.post("/unlock")
def unlock(payload: Payload) -> dict:
    if payload.code == UNLOCK_CODE:
        raise RuntimeError("planted bug")
    return {"ok": True}
