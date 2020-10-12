from collections import defaultdict

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from ..utils import OpenAPIVersion


class User(BaseModel):
    username: str = Field(min_length=3)

    class Config:
        extra = "forbid"


class Message(BaseModel):
    detail: str

    class Config:
        extra = "forbid"


def create_app(endpoints=("root",), version=OpenAPIVersion("3.0")):
    if version != OpenAPIVersion("3.0"):
        raise ValueError("FastAPI supports only Open API 3.0")
    app = FastAPI()
    users = {}
    history = defaultdict(list)

    if "root" in endpoints:

        @app.get("/users")
        async def root():
            return {"success": True}

    if "create_user" in endpoints:

        @app.post("/users/", status_code=201)
        def create_user(user: User):
            user_id = len(users) + 1
            users[user_id] = {**user.dict(), "id": user_id}
            history[user_id].append("POST")
            return {"id": user_id}

    if "get_user" in endpoints:

        @app.get("/users/{user_id}", responses={404: {"model": Message}})
        def get_user(user_id: int, uid: int = Query(...), code: int = Query(...)):
            try:
                user = users[user_id]
                history[user_id].append("GET")
                return user
            except KeyError:
                raise HTTPException(status_code=404, detail="Not found")

    if "update_user" in endpoints:

        @app.patch("/users/{user_id}", responses={404: {"model": Message}})
        def update_user(user_id: int, update: User, common: int = Query(...)):
            try:
                user = users[user_id]
                history[user_id].append("PATCH")
                if history[user_id] == ["POST", "GET", "PATCH", "GET", "PATCH"]:
                    raise HTTPException(status_code=500)
                user["username"] = update.username
                return user
            except KeyError:
                raise HTTPException(status_code=404, detail="Not found")

    return app
