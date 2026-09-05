"""API routes for agent status, action history, and privacy settings."""

from flask import Flask, jsonify, request


def register_routes(app: Flask) -> None:
    @app.route("/api/agent/status", methods=["GET"])
    def agent_status():
        return jsonify({"status": "idle"})

    @app.route("/api/agent/history", methods=["GET"])
    def agent_history():
        return jsonify({"actions": []})

    @app.route("/api/agent/step", methods=["POST"])
    def agent_step():
        # Lazy-load agent modules only when needed
        from agent.perception import Perception
        from agent.decision_engine import DecisionEngine
        from agent.action_planner import ActionPlanner
        from agent.browser_controller import BrowserController

        perception = Perception()
        decision_engine = DecisionEngine()
        planner = ActionPlanner()
        controller = BrowserController()

        payload = request.get_json(force=True) or {}
        goal = payload.get("goal", "")
        dom_snapshot = payload.get("dom_snapshot", {})
        page_text = payload.get("page_text", "")

        observation = perception.observe(dom_snapshot, page_text)
        decision = decision_engine.decide_next_action(observation, goal, history=[])
        steps = planner.plan(decision)
        results = controller.execute(steps)

        return jsonify({
            "observation": observation,
            "decision": decision,
            "results": results,
        })

    @app.route("/api/privacy/status", methods=["GET"])
    def privacy_status():
        return jsonify({"pii_redaction": True, "face_blur": True})
            # In-memory store for the latest screenshot (base64 data URL)
    latest_screenshot = {"data": None}

    @app.route("/api/agent/screenshot", methods=["POST"])
    def receive_screenshot():
        payload = request.get_json(force=True) or {}
        latest_screenshot["data"] = payload.get("image")
        return jsonify({"status": "received"})

    @app.route("/api/agent/screenshot", methods=["GET"])
    def get_screenshot():
        return jsonify({"image": latest_screenshot["data"]})
