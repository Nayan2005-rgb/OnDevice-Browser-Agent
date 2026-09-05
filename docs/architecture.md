# Architecture

## Overview

The OnDevice Browser Agent is composed of five cooperating layers:

1. **Extension (Chrome, Manifest V3)** — captures page/DOM state and executes
   concrete browser actions (click, type, scroll, navigate).
2. **Vision** — on-device ONNX models for object/UI-element detection, face
   detection, and visual PII detection, plus a redaction utility.
3. **Privacy** — DOM- and text-level sensitive-data detectors and a sanitizer
   that redacts findings before anything is sent onward.
4. **Agent** — the core loop: `perception → decision_engine → action_planner
   → browser_controller`.
5. **Server** — a local Flask API that the frontend dashboard and extension
   talk to, plus an LLM service abstraction used by the decision engine.

## Data Flow

```
Extension (content.js + privacyFilter.js)
        │  sanitized DOM snapshot / page text
        ▼
agent/perception.py  ──uses──▶ privacy/*  and  vision/*
        │  observation (screenshot, ui_elements, sanitized_text)
        ▼
agent/decision_engine.py  ──calls──▶ server/llm_service.py
        │  decision (action, target, reasoning)
        ▼
agent/action_planner.py
        │  ordered list of concrete steps
        ▼
agent/browser_controller.py  ──sends──▶ extension/browserActions.js
        │  executes in the real page
        ▼
Result flows back up to server/routes.py → frontend dashboard
```

## Privacy-by-design principle

No raw screenshot or DOM content should leave the device unredacted. Every
observation passes through `privacy/sensitive_data_detector.py` and
`privacy/sanitizer.py` (text) and `vision/redaction.py` (images) before it is
handed to the decision engine or logged in action history.
