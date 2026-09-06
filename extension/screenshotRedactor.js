/**
 * Local screenshot redaction for the extension service worker.
 *
 * PRIVACY BOUNDARY:
 *   RAW screenshot  →  (this module)  →  SANITIZED screenshot  →  network
 * The original image must never leave the device when privacy mode is on.
 */

/* global OffscreenCanvas, createImageBitmap */

const HIGH_RISK = new Set(['password', 'credit_card', 'ssn', 'sensitive']);

const DEFAULT_FACE_CONFIG = {
  faceDetection: true,
  minConfidence: 0.6,
  paddingPercent: 10,
  blurStrength: 'adaptive',
};

/**
 * Choose redaction strategy for a sensitive category.
 * High-risk → solid black box; faces / lower-risk PII → blur.
 */
function selectRedactionStrategy(category) {
  const cat = String(category || '').toLowerCase();
  if (HIGH_RISK.has(cat)) return 'black_box';
  return 'blur';
}

/**
 * Map a DOM viewport box (CSS pixels) onto screenshot pixel space.
 * Screenshot size may differ from CSS viewport (devicePixelRatio, etc.).
 */
function mapViewportBoxToScreenshot(box, viewport, screenshot) {
  const vw = Math.max(1, viewport.width || 1);
  const vh = Math.max(1, viewport.height || 1);
  const scaleX = (screenshot.width || vw) / vw;
  const scaleY = (screenshot.height || vh) / vh;

  return {
    x: Math.round((box.x || 0) * scaleX),
    y: Math.round((box.y || 0) * scaleY),
    width: Math.max(1, Math.round((box.width || 0) * scaleX)),
    height: Math.max(1, Math.round((box.height || 0) * scaleY)),
  };
}

function clampBox(box, width, height) {
  const x1 = Math.max(0, Math.min(width, Math.round(box.x)));
  const y1 = Math.max(0, Math.min(height, Math.round(box.y)));
  const x2 = Math.max(x1, Math.min(width, x1 + Math.round(box.width)));
  const y2 = Math.max(y1, Math.min(height, y1 + Math.round(box.height)));
  return { x: x1, y: y1, width: Math.max(1, x2 - x1), height: Math.max(1, y2 - y1) };
}

function expandBoxWithPadding(box, paddingPercent, width, height) {
  const padX = (box.width || 0) * ((paddingPercent || 0) / 100);
  const padY = (box.height || 0) * ((paddingPercent || 0) / 100);
  return clampBox(
    {
      x: box.x - padX,
      y: box.y - padY,
      width: box.width + padX * 2,
      height: box.height + padY * 2,
    },
    width,
    height
  );
}

function adaptiveFaceBlurFactor(width, height) {
  const size = Math.max(width, height);
  // Small face → milder pixelation; large face → stronger
  if (size < 40) return 6;
  if (size < 80) return 8;
  if (size < 160) return 12;
  return 16;
}

function applyBlackBox(ctx, box) {
  ctx.fillStyle = '#000000';
  ctx.fillRect(box.x, box.y, box.width, box.height);
}

/**
 * Approximate blur by downscaling the region and stretching it back.
 * Works in OffscreenCanvas without CSS filter dependencies.
 */
function applyBlur(ctx, box, factor) {
  const { x, y, width, height } = box;
  if (width < 2 || height < 2) {
    applyBlackBox(ctx, box);
    return;
  }

  const f = Math.max(2, factor || 8);
  const sw = Math.max(1, Math.floor(width / f));
  const sh = Math.max(1, Math.floor(height / f));

  try {
    const tmp = new OffscreenCanvas(sw, sh);
    const tctx = tmp.getContext('2d');
    tctx.imageSmoothingEnabled = true;
    tctx.drawImage(ctx.canvas, x, y, width, height, 0, 0, sw, sh);
    ctx.imageSmoothingEnabled = true;
    ctx.drawImage(tmp, 0, 0, sw, sh, x, y, width, height);
  } catch (_) {
    // Fallback: darken region if OffscreenCanvas draw fails
    ctx.fillStyle = 'rgba(0,0,0,0.65)';
    ctx.fillRect(x, y, width, height);
  }
}

/**
 * Redact a screenshot data URL using unified sensitive regions.
 *
 * @param {string} dataUrl - raw PNG data URL (stays local)
 * @param {Array} boxes - regions in CSS viewport coords (DOM) OR screenshot
 *   pixel coords when options.boxesInScreenshotSpace is true (faces).
 * @param {object} viewport - {width, height, devicePixelRatio}
 * @param {object} [options]
 * @returns {Promise<{sanitizedDataUrl, report, metrics}>}
 */
async function redactScreenshot(dataUrl, boxes, viewport, options) {
  const opts = options || {};
  const faceConfig = { ...DEFAULT_FACE_CONFIG, ...(opts.faceConfig || {}) };
  const t0 = performance.now();
  const tMapStart = performance.now();

  const response = await fetch(dataUrl);
  const blob = await response.blob();
  const bitmap = await createImageBitmap(blob);

  const screenshotW = bitmap.width;
  const screenshotH = bitmap.height;
  const vp = {
    width: (viewport && viewport.width) || screenshotW,
    height: (viewport && viewport.height) || screenshotH,
    devicePixelRatio: (viewport && viewport.devicePixelRatio) || 1,
  };

  const canvas = new OffscreenCanvas(screenshotW, screenshotH);
  const ctx = canvas.getContext('2d');
  ctx.drawImage(bitmap, 0, 0);
  bitmap.close();

  const mapped = [];
  for (const box of boxes || []) {
    const alreadyScreenshot = box.source === 'vision' || opts.boxesInScreenshotSpace;
    let scaled = alreadyScreenshot
      ? {
          x: box.x || 0,
          y: box.y || 0,
          width: box.width || 1,
          height: box.height || 1,
        }
      : mapViewportBoxToScreenshot(box, vp, {
          width: screenshotW,
          height: screenshotH,
        });

    let clamped = clampBox(scaled, screenshotW, screenshotH);
    const category = box.category || 'sensitive';
    const strategy = selectRedactionStrategy(category);

    if (category === 'face') {
      clamped = expandBoxWithPadding(
        clamped,
        faceConfig.paddingPercent,
        screenshotW,
        screenshotH
      );
    }

    mapped.push({
      category,
      strategy,
      source: box.source || (category === 'face' ? 'vision' : 'dom'),
      confidence: box.confidence,
      box: clamped,
      blurFactor:
        category === 'face'
          ? faceConfig.blurStrength === 'adaptive'
            ? adaptiveFaceBlurFactor(clamped.width, clamped.height)
            : Number(faceConfig.blurStrength) || 10
          : 8,
    });
  }
  const mappingMs = Math.round(performance.now() - tMapStart);

  const tRedactStart = performance.now();
  for (const item of mapped) {
    if (item.strategy === 'black_box') {
      applyBlackBox(ctx, item.box);
    } else {
      applyBlur(ctx, item.box, item.blurFactor);
    }
  }
  const redactionMs = Math.round(performance.now() - tRedactStart);

  const outBlob = await canvas.convertToBlob({ type: 'image/png' });
  const sanitizedDataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('Failed to encode sanitized screenshot'));
    reader.readAsDataURL(outBlob);
  });

  const totalMs = Math.round(performance.now() - t0);
  const categoryCounts = {};
  let facesDetected = 0;
  let facesRedacted = 0;
  for (const item of mapped) {
    categoryCounts[item.category] = (categoryCounts[item.category] || 0) + 1;
    if (item.category === 'face') {
      facesDetected += 1;
      facesRedacted += 1;
    }
  }

  const report = {
    screenshot_width: screenshotW,
    screenshot_height: screenshotH,
    redactions: mapped.map((item) => ({
      category: item.category,
      strategy: item.strategy,
      source: item.source,
      box: { ...item.box },
    })),
    total_redactions: mapped.length,
    processing_time_ms: totalMs,
    categories: categoryCounts,
    dom_pii: {
      emails: categoryCounts.email || 0,
      phones: categoryCounts.phone || 0,
      passwords: categoryCounts.password || 0,
    },
    vision: {
      faces_detected: facesDetected,
      faces_redacted: facesRedacted,
    },
    // Never include original sensitive text or face image data
  };

  return {
    sanitizedDataUrl,
    report,
    metrics: {
      coordinate_mapping_ms: mappingMs,
      image_redaction_ms: redactionMs,
      total_privacy_processing_ms: totalMs,
    },
  };
}

// Expose for importScripts in the service worker
self.ScreenshotRedactor = {
  selectRedactionStrategy,
  mapViewportBoxToScreenshot,
  expandBoxWithPadding,
  adaptiveFaceBlurFactor,
  redactScreenshot,
  DEFAULT_FACE_CONFIG,
};
