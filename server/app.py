"""Local Flask server: exposes the agent's API to the frontend dashboard
and the browser extension."""

from flask import Flask
from flask_cors import CORS

from server.routes import register_routes


def create_app(*, hydrate_sessions: bool = False) -> Flask:
    app = Flask(__name__)
    CORS(app)
    register_routes(app)

    # Milestone 5A/5B: initialize local SQLite (schema only). Full hydration
    # runs on server start (see __main__) so pytest stays isolated.
    try:
        from storage.database import get_database

        get_database()
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("Database init skipped: %s", exc)

    if hydrate_sessions:
        try:
            from agent.session_manager import get_session_manager

            sm = get_session_manager()
            if sm.storage_available():
                restored = sm.hydrate_active_sessions()
                if restored:
                    from server import routes as routes_mod

                    latest = restored[0]
                    routes_mod._agent_state["session_id"] = latest.session_id
                    if latest.plan_id:
                        routes_mod._agent_state["plan_id"] = latest.plan_id
                    routes_mod._set_status(
                        latest.status,
                        latest.goal or routes_mod._agent_state.get("last_task"),
                    )
        except Exception as exc:  # noqa: BLE001 — startup must not crash
            app.logger.warning("Session hydration skipped: %s", exc)

        # Milestone 5B: durable confirmations / deliveries / lifecycles
        try:
            from agent.durable_lifecycle import hydrate_durable_lifecycle

            stats = hydrate_durable_lifecycle()
            app.logger.info("Durable action lifecycle hydrated: %s", stats)
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("Durable lifecycle hydration skipped: %s", exc)

    return app


if __name__ == "__main__":
    app = create_app(hydrate_sessions=True)
    app.run(host="127.0.0.1", port=5000, debug=True)
