from schemathesis.runner import execute


def test_runner(swagger_20):
    execute("http://127.0.0.1:8000", swagger_20)
