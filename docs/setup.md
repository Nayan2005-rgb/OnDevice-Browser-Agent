# Setup

## Prerequisites

- Node.js 18+
- Python 3.10+
- Google Chrome (for loading the unpacked extension)

## 1. Frontend

```bash
npm install
npm run dev
```

Dashboard will be available at `http://localhost:5173`.

## 2. Backend

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python server/app.py
```

API will be available at `http://localhost:5000`.

## 3. Browser Extension

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Click **Load unpacked** and select the `extension/` directory.
4. Pin the extension and open the popup to start the agent.

## 4. Models

Place ONNX weights in:

- `vision/model/vision_model.onnx`
- `models/vision/lightweight_model.onnx`
- `models/face_detection/face_model.onnx`
- `models/pii_detection/pii_model/`

These are gitignored by default — download or train them separately.

## 5. Tests

```bash
pytest tests/
```
