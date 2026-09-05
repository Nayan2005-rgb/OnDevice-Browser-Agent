"""Local Flask server: exposes the agent's API to the frontend dashboard
and the browser extension."""

from flask import Flask
from flask_cors import CORS

from server.routes import register_routes


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)
    register_routes(app)
    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=5000, debug=True)
