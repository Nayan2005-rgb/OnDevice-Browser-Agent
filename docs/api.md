# API Reference

Base URL: `http://localhost:5000/api`

## GET /agent/status
Returns the current agent status.

```json
{ "status": "idle" }
```

## GET /agent/history
Returns the list of past executed actions.

```json
{ "actions": [] }
```

## POST /agent/step
Runs one perception → decision → planning → execution cycle.

**Request body**
```json
{
  "goal": "Log into the site",
  "dom_snapshot": {},
  "page_text": "..."
}
```

**Response**
```json
{
  "observation": { "...": "..." },
  "decision": { "action": "click", "target": "#submit" },
  "results": [{ "step": { "...": "..." }, "status": "ok" }]
}
```

## GET /privacy/status
Returns current privacy filter toggles.

```json
{ "pii_redaction": true, "face_blur": true }
```
