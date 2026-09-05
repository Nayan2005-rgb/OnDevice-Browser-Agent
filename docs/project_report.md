# Project Report

## Summary

OnDevice Browser Agent is a browser automation agent designed around a
privacy-first, on-device processing model: screenshots and DOM content are
analyzed and redacted locally (PII detection, face detection) before any
data is passed to a decision-making LLM.

## Status

This scaffold includes the full module layout and interface stubs for:

- Frontend dashboard (React + Vite + Tailwind)
- Chrome extension (Manifest V3)
- Vision pipeline (capture, detection, redaction)
- Privacy pipeline (DOM + text PII detection, sanitization)
- Agent loop (perception, decision engine, action planner, browser controller)
- Local Flask server and routes
- Unit test scaffolding

## Next Steps

- Wire up real screenshot capture in `vision/screenshot_capture.py`.
- Train or source ONNX models for UI-element, face, and PII detection.
- Implement the extension ↔ backend transport (e.g. native messaging or a
  local WebSocket) referenced in `agent/browser_controller.py`.
- Connect `server/llm_service.py` to a chosen LLM provider.
- Expand test coverage once real model outputs are available.
