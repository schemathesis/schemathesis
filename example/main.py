import logging
import os

from app.app import create_app

logging.basicConfig(level=logging.INFO)
DEFAULT_DB_URL = "postgresql://test:test@localhost:5432/schemathesis-example"


def main(port, db_url):
    app = create_app({"DB_URL": db_url})
    app.run(port, debug=True, use_default_access_log=True)


if __name__ == "__main__":
    DB_URL = os.getenv("DB_URL", DEFAULT_DB_URL)
    PORT = int(os.getenv("APP_PORT", 5000))
    main(port=PORT, db_url=DB_URL)
