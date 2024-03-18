import pytest
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
    result = cli.run("/openapi.json", "--app", f"{module.purebasename}:app", "--force-schema-version=30")
    assert result.exit_code == ExitCode.TESTS_FAILED, result.stdout
    assert "1 passed, 1 failed in" in result.stdout


@pytest.mark.parametrize("workers", (1, 2))
def test_cli_run_output_success(testdir, cli, workers):
    module = testdir.make_importable_pyfile(
        location="""
            from fastapi import FastAPI
            from fastapi import HTTPException

            app = FastAPI()

            @app.get("/api/success")
            async def success():
                return {"success": True}

            """
    )
    result = cli.run(
        "/openapi.json",
        "--app",
        f"{module.purebasename}:app",
        f"--workers={workers}",
        "--show-trace",
        "--force-schema-version=30",
    )

    assert result.exit_code == ExitCode.OK, result.stdout
    lines = result.stdout.split("\n")
    assert lines[5] == f"Workers: {workers}"
    if workers == 1:
        assert lines[11].startswith("GET /api/success .")
    else:
        assert lines[11] == "."
    assert " HYPOTHESIS OUTPUT " not in result.stdout
    assert " SUMMARY " in result.stdout

    lines = result.stdout.strip().split("\n")
    last_line = lines[-1]
    assert "== 1 passed in " in last_line
    # And the running time is a small positive number
    time = float(last_line.split(" ")[-2].replace("s", ""))
    assert 0 <= time < 5
