import logging

from app.app import create_app
from app.settings import load_config

logging.basicConfig(level=logging.INFO)


def main(config_path, port):
    config = load_config(config_path)
    app = create_app(config)
    app.run(port, debug=True, use_default_access_log=True)


if __name__ == "__main__":
    main("config.json", port=5000)
