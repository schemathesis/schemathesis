from fastapi import FastAPI


def create_app():
    app = FastAPI()

    @app.get("/users")
    async def root():
        return {"success": True}

    return app
