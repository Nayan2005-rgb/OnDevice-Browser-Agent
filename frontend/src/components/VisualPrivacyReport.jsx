import React, { useEffect, useState } from 'react';

/**
 * Visual privacy report for the latest sanitized screenshot.
 * Server only ever holds the sanitized image + metadata (no raw capture).
 */
export default function VisualPrivacyReport() {
  const [report, setReport] = useState(null);
  const [sanitized, setSanitized] = useState(false);
  const [visualContext, setVisualContext] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchReport = () => {
      fetch('/api/agent/screenshot')
        .then((res) => res.json())
        .then((data) => {
          setSanitized(Boolean(data.sanitized));
          setReport(data.screenshot_privacy_report || null);
          setVisualContext(data.visual_context || null);
        })
        .catch((err) => setError(err.message));
    };

    fetchReport();
    const interval = setInterval(fetchReport, 3000);
    return () => clearInterval(interval);
  }, []);

  const categories = (report && report.categories) || {};
  const timing = (report && report.timing) || {};
  const vision = (report && report.vision) || {};
  const domPii = (report && report.dom_pii) || {};
  const total = (report && report.total_redactions) || 0;
  const processingMs =
    (report && report.processing_time_ms) ||
    timing.total_privacy_processing_ms ||
    null;

  const facesDetected =
    vision.faces_detected != null
      ? vision.faces_detected
      : categories.face || (visualContext && visualContext.faces_detected) || 0;
  const facesRedacted =
    vision.faces_redacted != null ? vision.faces_redacted : facesDetected;
  const faceDetectMs =
    timing.face_detection_ms != null ? timing.face_detection_ms : null;

  const emails = domPii.emails != null ? domPii.emails : categories.email || 0;
  const phones = domPii.phones != null ? domPii.phones : categories.phone || 0;
  const passwords =
    domPii.passwords != null ? domPii.passwords : categories.password || 0;

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <h2 className="font-semibold mb-2">Visual Privacy Report</h2>
      <p className="text-xs text-slate-500 mb-3">
        Original Screenshot (local-only) → Face + PII Redaction → Sanitized Screenshot
      </p>

      {error && <p className="text-sm text-red-500">Error: {error}</p>}

      {!error && !report && (
        <p className="text-sm text-slate-500">No screenshot privacy report yet.</p>
      )}

      {!error && report && (
        <div className="text-sm space-y-1 text-slate-700">
          <div className="flex justify-between">
            <span>Emails Redacted</span>
            <span>{emails}</span>
          </div>
          <div className="flex justify-between">
            <span>Phones Redacted</span>
            <span>{phones}</span>
          </div>
          <div className="flex justify-between">
            <span>Passwords Hidden</span>
            <span>{passwords}</span>
          </div>
          <div className="flex justify-between">
            <span>Cards / SSN hidden</span>
            <span>{(categories.credit_card || 0) + (categories.ssn || 0)}</span>
          </div>
          <div className="flex justify-between">
            <span>Faces Detected</span>
            <span>{facesDetected}</span>
          </div>
          <div className="flex justify-between">
            <span>Faces Redacted</span>
            <span>{facesRedacted}</span>
          </div>
          <hr className="my-2 border-slate-200" />
          <div className="flex justify-between font-medium">
            <span>Total Redactions</span>
            <span>{total}</span>
          </div>
          <div className="flex justify-between">
            <span>Face Detection</span>
            <span>{faceDetectMs != null ? `${faceDetectMs} ms` : '—'}</span>
          </div>
          <div className="flex justify-between">
            <span>Processing time</span>
            <span>{processingMs != null ? `${processingMs} ms` : '—'}</span>
          </div>
          {(report.screenshot_width || report.screenshot_height) && (
            <div className="flex justify-between">
              <span>Screenshot size</span>
              <span>
                {report.screenshot_width} × {report.screenshot_height}
              </span>
            </div>
          )}
          <div className="flex justify-between mt-2">
            <span>Visual Privacy Status</span>
            <span className={sanitized ? 'text-green-700 font-semibold' : 'text-amber-700'}>
              {sanitized ? 'SAFE ✓' : 'UNKNOWN'}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
