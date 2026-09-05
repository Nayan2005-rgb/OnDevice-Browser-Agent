# OnDevice Browser Agent

An on-device browser automation agent that perceives the screen, plans actions,
and executes them in the browser — with a strong emphasis on local, privacy-preserving
processing (PII detection, face detection, and redaction happen on-device before
any data leaves the machine).

## Project Structure

- `frontend/` — React + Vite dashboard UI for monitoring agent status, privacy filters, and action history.
- `extension/` — Chrome extension (manifest v3) that captures the page/screen and executes browser actions.
- `vision/` — On-device vision models: screenshot capture, object/UI element detection, PII & face detection, redaction.
- `privacy/` — DOM-level and content-level privacy detectors and sanitizers.
- `agent/` — Core agent loop: perception → decision engine → action planner → browser controller.
- `server/` — Local backend service (Flask/FastAPI-style) exposing routes and LLM integration.
- `models/` — ONNX model weights for vision, face detection, and PII detection.
- `tests/` — Unit tests for vision, PII detection, redaction, and the agent loop.
- `docs/` — Architecture, setup, and API documentation.
- `demo/` — Demo screenshots and video.

## Getting Started

See `docs/setup.md` for full setup instructions.

```bash
# Install frontend deps
npm install

# Install backend deps
pip install -r requirements.txt

# Run frontend dev server
npm run dev

# Run backend server
python server/app.py
```

## License

TBD
