import os

HERE = os.path.dirname(os.path.abspath(__file__))


def get_schema_path(schema_name):
    return os.path.join(HERE, "data", schema_name)


SIMPLE_PATH = get_schema_path("simple_swagger.yaml")
