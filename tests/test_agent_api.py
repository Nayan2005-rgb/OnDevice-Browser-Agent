"""Integration-style tests for /api/agent/step response schema."""

import pytest

from server.app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def _sample_payload(task: str):
    return {
        "task": task,
        "page": {
            "url": "https://example.com",
            "title": "Example",
            "visibleText": "Email: [EMAIL_REDACTED] Submit your application",
            "elements": [
                {
                    "tag": "button",
                    "text": "Submit",
                    "selector": "#submit-button",
                    "type": None,
                    "name": None,
                    "id": "submit-button",
                    "placeholder": None,
                    "ariaLabel": None,
                    "sensitive": False,
                },
                {
                    "tag": "input",
                    "text": "",
                    "selector": "#search",
                    "type": "text",
                    "name": "q",
                    "id": "search",
                    "placeholder": "Search",
                    "ariaLabel": "search box",
                    "sensitive": False,
                },
            ],
        },
        "privacy_report": {
            "emails_detected": 1,
            "phones_detected": 0,
            "password_fields_detected": 1,
            "cards_detected": 0,
            "total_redactions": 2,
        },
    }


def test_agent_step_click_response_schema(client):
    res = client.post("/api/agent/step", json=_sample_payload("Click the Submit button"))
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert data["action"]["type"] == "click"
    assert data["action"]["selector"] == "#submit-button"
    assert "reason" in data


def test_agent_step_type_response(client):
    res = client.post("/api/agent/step", json=_sample_payload("Type hello in the search box"))
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert data["action"]["type"] == "type"
    assert data["action"]["selector"] == "#search"
    assert data["action"]["text"] == "hello"


def test_agent_step_scroll_response(client):
    res = client.post("/api/agent/step", json=_sample_payload("Scroll down"))
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "success"
    assert data["action"]["type"] == "scroll"
    assert data["action"]["direction"] == "down"


def test_agent_step_no_action(client):
    res = client.post(
        "/api/agent/step", json=_sample_payload("Click the Missing Thing")
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "no_action"
    assert data["action"] is None


def test_agent_status_and_history_update(client):
    client.post("/api/agent/step", json=_sample_payload("Click the Submit button"))
    status = client.get("/api/agent/status").get_json()
    assert status["status"] in ("action_decided", "success", "idle", "no_action")
    history = client.get("/api/agent/history").get_json()
    assert len(history["actions"]) >= 1
    assert history["actions"][0]["task"] == "Click the Submit button"
