from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from ..schema import OpenAPIVersion


class User(BaseModel):
    first_name: str = Field(min_length=3)
    last_name: str = Field(min_length=3)
    model_config = ConfigDict(extra="forbid")


class BuggyUser(BaseModel):
    first_name: str = Field(min_length=3)
    last_name: Optional[str] = Field(None, min_length=3, json_schema_extra={"nullable": True})
    model_config = ConfigDict(extra="forbid")


class Message(BaseModel):
    detail: str
    model_config = ConfigDict(extra="forbid")


def create_app(operations=("root",), version=OpenAPIVersion("3.0")):
    if version != OpenAPIVersion("3.0"):
        raise ValueError("FastAPI supports only Open API 3.0")
    app = FastAPI()
    users = {}

    if "root" in operations:

        @app.get("/users")
        async def root():
            return {"success": True}

    if "create_user" in operations:

        @app.post("/users/", status_code=201)
        def create_user(user: User):
            user_id = str(uuid4())
            users[user_id] = {**user.model_dump(), "id": user_id}
            return {"id": user_id}

    if "get_user" in operations:

        @app.get("/users/{user_id}", responses={404: {"model": Message}})
        def get_user(user_id: str, uid: str = Query(...), code: int = Query(...)):
            try:
                user = users[user_id]
                # The full name is done specifically via concatenation to trigger a bug when the last name is `None`
                try:
                    full_name = user["first_name"] + " " + user["last_name"]
                except TypeError:
                    # We test it via out ASGI integration and `TypeError` will be propagated otherwise.
                    # To keep the same behavior across all test server implementations we reraise it as a server error
                    raise HTTPException(status_code=500, detail="We got a problem!")  # noqa: B904
                return {"id": user["id"], "full_name": full_name}
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="Not found") from exc

    if "update_user" in operations:

        @app.patch("/users/{user_id}", responses={404: {"model": Message}})
        def update_user(user_id: str, update: BuggyUser, common: int = Query(...)):
            try:
                user = users[user_id]
                for field in ("first_name", "last_name"):
                    user[field] = getattr(update, field)
                return user
            except KeyError:
                raise HTTPException(status_code=404, detail="Not found")  # noqa: B904

    return app
