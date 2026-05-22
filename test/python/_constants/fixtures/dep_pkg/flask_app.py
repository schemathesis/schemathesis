from flask import Flask

from .shared import UNLOCK_TOKEN

app = Flask(__name__)


@app.route("/unlock")
def unlock():
    return {"token": UNLOCK_TOKEN}
