import os
import time

from flask import Flask, jsonify, request, Response
import sqlalchemy.exc
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
db = SQLAlchemy(app)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Column with restricted size
    text = db.Column(db.String(16), sqlalchemy.CheckConstraint("LENGTH(text) < 16"))


@app.route("/internal-server-errors/improper-unicode-encoding", methods=["POST"])
def improper_unicode_encoding():
    data = request.json
    if "text" not in data:
        return jsonify({"success": False, "error": "Missing text"}), 400

    try:
        # Simulating improper Unicode handling
        data["text"].encode("ascii")
        return jsonify({"success": True})
    except UnicodeDecodeError:
        return jsonify({"success": False, "error": "Unicode error"}), 500


@app.route("/internal-server-errors/improper-input-type-handling", methods=["POST"])
def improper_input_type_handling():
    data = request.json
    if not isinstance(data, dict) or "number" not in data:
        return jsonify({"success": False}), 400

    # Potential crash point
    digits = [int(d) for d in str(data["number"])]

    # Luhn algorithm to validate card number
    even_digits_sum = sum(digits[-1::-2])
    odd_digits_sum = sum(sum(divmod(d * 2, 10)) for d in digits[-2::-2])
    checksum = even_digits_sum + odd_digits_sum

    is_valid = checksum % 10 == 0
    return jsonify({"success": is_valid})


@app.route("/internal-server-errors/exceeding-column-size", methods=["POST"])
def exceeding_column_size():
    data = request.json
    if "text" not in data:
        return jsonify({"success": False}), 400

    # Storing input that exceeds the column size limit can result in a database error
    message = Message(text=data["text"])
    db.session.add(message)
    try:
        db.session.commit()
        return jsonify({"success": True})
    except sqlalchemy.exc.SQLAlchemyError as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/response-conformance/malformed-json", methods=["GET"])
def malformed_json():
    return "{success: true}", 200, {"Content-Type": "application/json"}


@app.route("/response-conformance/incorrect-content-type", methods=["GET"])
def incorrect_content_type():
    # This response does not conform to the OpenAPI schema
    return "Success!", 200, {"Content-Type": "text/plain"}


@app.route("/response-conformance/missing-field", methods=["GET"])
def missing_field():
    response_data = {
        "id": "123",
        "name": "Alice",
        # "age" field is missing
    }
    return jsonify(response_data), 200


# Simulating a database with a dictionary
data_db = {"0": "Data for ID 0"}


@app.route("/response-conformance/undocumented-status-code", methods=["GET"])
def undocumented_status_code():
    id = request.args.get("id")
    if id is None:
        return jsonify({"error": "ID is required"}), 400

    data = data_db.get(str(id))
    if data is None:
        # Returning a 404 status code, which is not documented in the API schema
        return jsonify({"error": "Not Found"}), 404

    return jsonify({"message": data})


# Maximum number of items that can be fetched in one request
MAX_ITEMS = 120

# Simulated average delay per item retrieval in seconds
DELAY_PER_ITEM = 0.001  # 1 ms


@app.route("/performance/unbounded-result-set", methods=["GET"])
def unbounded_result_set():
    limit = min(request.args.get("limit", default=MAX_ITEMS, type=int), MAX_ITEMS)

    if limit <= 0:
        return jsonify({"error": "Limit must be greater than 0"}), 400

    # Simulate fetching 'limit' number of items with a delay for each item retrieval.
    # The delay simulates the additional time required for transferring more data.
    items = {}
    for idx in range(limit):
        # Simulate the delay for retrieving each item
        time.sleep(DELAY_PER_ITEM)

        # Simulate the generation of an item
        items[f"i_{idx}"] = idx

    # It's crucial to limit the number of items fetched in one request to prevent server strain and
    # maintain optimal performance.
    return jsonify(items)


# Set a reasonable limit for the maximum number of Fibonacci numbers to generate
MAX_N = 100000


def generate_fibonacci(n):
    # The loop generates Fibonacci numbers inefficiently, leading to increased response times for large n.
    fib_sequence = [0, 1]
    while len(fib_sequence) < n:
        fib_sequence.append(fib_sequence[-1] + fib_sequence[-2])
    return fib_sequence


@app.route("/performance/inefficient-algorithm", methods=["GET"])
def inefficient_algorithm():
    n = request.args.get("n", type=int)
    search_term = request.args.get("searchTerm", type=int)

    if n is None or search_term is None:
        return jsonify({"error": "Missing required parameters"}), 400

    if n > MAX_N:
        return jsonify({"error": f"n should be less than or equal to {MAX_N}"}), 400

    # Generating a large Fibonacci sequence
    fib_sequence = generate_fibonacci(n)

    # Searching for the term in the sequence
    found_indices = [index for index, value in enumerate(fib_sequence) if value == search_term]

    return jsonify({"foundAt": found_indices})


with open("openapi.json") as fd:
    RAW_SCHEMA = fd.read()


@app.route("/openapi.json")
def openapi():
    return Response(RAW_SCHEMA, content_type="application/json")


PORT = int(os.getenv("FLASK_RUN_PORT", 5123))


@app.route("/ui/")
def ui():
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Swagger UI</title>
    <link href="https://unpkg.com/swagger-ui-dist@3/swagger-ui.css" rel="stylesheet" type="text/css" media="all">
    <script src="https://unpkg.com/swagger-ui-dist@3/swagger-ui-bundle.js" charset="UTF-8"></script>
</head>

<body>
<div id="swagger-ui"></div>
<script>
    window.onload = function () {{
        window.ui = SwaggerUIBundle({{
            url: "http://127.0.0.1:{PORT}/openapi.json",
            dom_id: '#swagger-ui',
        }})
    }}
</script>
</body>
</html>"""


@app.errorhandler(500)
def handle_500(error):
    exception = error.original_exception
    if exception:
        error = str(exception)
    else:
        error = None
    return jsonify({"success": False, "error": error}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
