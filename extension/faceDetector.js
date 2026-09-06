/**
 * Local face detection client for the extension service worker.
 *
 * FaceDetector interface (JS):
 *   detect(imageDataUrl, options) → Promise<{faces, face_detection_ms}>
 *
 * Implementation: LocalApiFaceDetector
 *   Calls the on-device Flask privacy worker (/api/privacy/detect-faces).
 *   The raw image is processed ephemerally in local memory and NEVER stored.
 *   Only bounding boxes return — no crops, embeddings, or identity.
 *
 * Future backends (ONNX / WebGPU / MediaPipe) can replace LocalApiFaceDetector
 * without changing background.js / screenshotRedactor.js.
 */

/* global fetch */

const FACE_API = 'http://127.0.0.1:5000/api/privacy/detect-faces';

const DEFAULT_FACE_OPTIONS = {
  faceDetection: true,
  minConfidence: 0.6,
  paddingPercent: 10,
  blurStrength: 'adaptive',
};

/**
 * @param {string} imageDataUrl
 * @param {object} [options]
 * @returns {Promise<{faces: Array, face_detection_ms: number, ok: boolean}>}
 */
async function detectFaces(imageDataUrl, options) {
  const opts = { ...DEFAULT_FACE_OPTIONS, ...(options || {}) };
  if (opts.faceDetection === false) {
    return { ok: true, faces: [], face_detection_ms: 0 };
  }

  const t0 = performance.now();
  try {
    const response = await fetch(FACE_API, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image: imageDataUrl,
        minConfidence: opts.minConfidence,
        // Explicit: never ask the worker to persist
        persist: false,
      }),
    });
    const data = await response.json();
    const ms = Math.round(performance.now() - t0);
    if (!response.ok || !data.ok) {
      return { ok: false, faces: [], face_detection_ms: ms, error: data.error };
    }
    // Normalize: only geometry + confidence
    const faces = (data.faces || []).map((f) => ({
      x: f.x,
      y: f.y,
      width: f.width,
      height: f.height,
      confidence: f.confidence,
      category: 'face',
      source: 'vision',
    }));
    return { ok: true, faces, face_detection_ms: ms };
  } catch (err) {
    return {
      ok: false,
      faces: [],
      face_detection_ms: Math.round(performance.now() - t0),
      error: err && err.message ? err.message : 'face_detect_failed',
    };
  }
}

/**
 * Merge DOM-sensitive boxes with face boxes into unified regions.
 * DOM boxes are CSS-viewport; face boxes are screenshot-pixel space.
 */
function mergeSensitiveRegions(domBoxes, faceBoxes) {
  const regions = [];
  for (const box of domBoxes || []) {
    regions.push({
      category: box.category || 'sensitive',
      source: box.source || 'dom',
      x: box.x,
      y: box.y,
      width: box.width,
      height: box.height,
    });
  }
  for (const box of faceBoxes || []) {
    regions.push({
      category: 'face',
      source: 'vision',
      x: box.x,
      y: box.y,
      width: box.width,
      height: box.height,
      confidence: box.confidence,
    });
  }
  return regions;
}

function buildVisualContext(report, timing) {
  const vision = (report && report.vision) || {};
  const cats = (report && report.categories) || {};
  const faces = vision.faces_detected != null ? vision.faces_detected : cats.face || 0;
  const total = (report && report.total_redactions) || 0;
  return {
    screenshot_available: true,
    dimensions: {
      width: report && report.screenshot_width,
      height: report && report.screenshot_height,
    },
    faces_detected: faces,
    faces_redacted: vision.faces_redacted != null ? vision.faces_redacted : faces,
    sensitive_regions: total,
    redactions_applied: total,
    privacy_safe: true,
    timing: timing || (report && report.timing) || undefined,
  };
}

self.FaceDetector = {
  detectFaces,
  mergeSensitiveRegions,
  buildVisualContext,
  DEFAULT_FACE_OPTIONS,
};
