from _pytest.main import ExitCode


def test_wsgi_app(testdir, cli):
    module = testdir.make_importable_pyfile(
        location="""
        from fastapi import FastAPI
        from fastapi import HTTPException

        app = FastAPI()

        @app.get("/api/success")
        async def success():
            return {"success": True}

        @app.get("/api/failure")
        async def failure():
            raise HTTPException(status_code=500)
            return {"failure": True}
        """
    )
    result = cli.run("/openapi.json", "--app", f"{module.purebasename}:app")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert "1 passed, 1 failed in" in result.stdout
