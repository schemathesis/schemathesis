import connexion

from .db import init_db


def create_app(config):
    connexion_app = connexion.AioHttpApp(__name__)
    connexion_app.add_api("openapi.yaml", pass_context_arg_name="request")
    connexion_app.app["config"] = config
    connexion_app.app.cleanup_ctx.append(init_db)
    return connexion_app
