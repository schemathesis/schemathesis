import json


def scenario(label, cases, interactions, *, timestamp=100.5):
    return {
        "ScenarioFinished": {
            "timestamp": timestamp,
            "recorder": {"label": label, "cases": cases, "interactions": interactions},
        }
    }


def write_run(path, payloads):
    lines = [json.dumps({"Initialize": {"command": "x", "schemathesis_version": "t", "seed": 0}})]
    lines.extend(json.dumps(payload) for payload in payloads)
    path.write_text("\n".join(lines) + "\n")
