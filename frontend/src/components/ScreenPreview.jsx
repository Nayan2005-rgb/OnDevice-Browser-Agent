import React, { useEffect, useMemo, useState } from 'react';

const VIEWS = [
  { id: 'screenshot', label: 'Screenshot' },
  { id: 'dom', label: 'DOM' },
  { id: 'vision', label: 'Vision' },
  { id: 'fused', label: 'Fused' },
  { id: 'layout', label: 'Layout' },
  { id: 'target', label: 'Target' },
  { id: 'action', label: 'Action' },
];

const TYPE_COLORS = {
  button: '#2563eb',
  input: '#059669',
  link: '#7c3aed',
  image: '#d97706',
  dialog: '#dc2626',
  container: '#64748b',
  text: '#0f766e',
  unknown: '#94a3b8',
};

function sourceLabel(source) {
  if (source === 'dom+vision') return 'DOM + VISION';
  if (source === 'dom') return 'DOM';
  if (source === 'vision') return 'VISION';
  return String(source || '').toUpperCase();
}

function OverlayBoxes({ elements, naturalSize, viewBox }) {
  if (!elements || !elements.length || !naturalSize.width) return null;
  const sx = viewBox.width / naturalSize.width;
  const sy = viewBox.height / naturalSize.height;

  return (
    <svg
      className="absolute inset-0 w-full h-full pointer-events-none"
      viewBox={`0 0 ${viewBox.width} ${viewBox.height}`}
      preserveAspectRatio="xMidYMid meet"
    >
      {elements.map((el) => {
        const box = el.box || {};
        const x = (box.x || 0) * sx;
        const y = (box.y || 0) * sy;
        const w = Math.max(2, (box.width || 0) * sx);
        const h = Math.max(2, (box.height || 0) * sy);
        const color = TYPE_COLORS[el.type] || TYPE_COLORS.unknown;
        const conf = Math.round((el.confidence || 0) * 100);
        const label = `${String(el.type || 'unknown').toUpperCase()}  ${conf}%`;
        const sub = sourceLabel(el.source);
        return (
          <g key={el.id}>
            <rect
              x={x}
              y={y}
              width={w}
              height={h}
              fill={`${color}22`}
              stroke={color}
              strokeWidth={2}
              rx={3}
            />
            <rect
              x={x}
              y={Math.max(0, y - 28)}
              width={Math.max(90, Math.min(w, 160))}
              height={26}
              fill={color}
              opacity={0.92}
              rx={2}
            />
            <text
              x={x + 4}
              y={Math.max(10, y - 18)}
              fill="#fff"
              fontSize="10"
              fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
            >
              {label}
            </text>
            <text
              x={x + 4}
              y={Math.max(22, y - 6)}
              fill="#e2e8f0"
              fontSize="9"
              fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
            >
              {sub}
              {el.sensitive ? ' · REDACTED' : ''}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function ActionTargetOverlay({ target, naturalSize, viewBox }) {
  if (!target || !naturalSize.width) return null;
  const sx = viewBox.width / naturalSize.width;
  const sy = viewBox.height / naturalSize.height;
  const box = target.box || {};
  const hasBox = box.width != null && box.height != null;
  const click = target.click_point || null;

  const x = hasBox ? (box.x || 0) * sx : click ? click.x * sx - 40 : 0;
  const y = hasBox ? (box.y || 0) * sy : click ? click.y * sy - 30 : 0;
  const w = hasBox ? Math.max(40, (box.width || 0) * sx) : 120;
  const h = hasBox ? Math.max(30, (box.height || 0) * sy) : 70;
  const cx = click ? click.x * sx : x + w / 2;
  const cy = click ? click.y * sy : y + h / 2;
  const conf = Math.round((target.confidence || 0) * 100);
  const strategy = String(target.strategy || 'selector').toUpperCase();
  const execStatus = target.execution?.status;

  return (
    <svg
      className="absolute inset-0 w-full h-full pointer-events-none"
      viewBox={`0 0 ${viewBox.width} ${viewBox.height}`}
      preserveAspectRatio="xMidYMid meet"
    >
      {hasBox && (
        <rect
          x={x}
          y={y}
          width={w}
          height={h}
          fill="#0f172a22"
          stroke="#0f172a"
          strokeWidth={3}
          strokeDasharray="6 3"
          rx={4}
        />
      )}
      <rect
        x={Math.max(0, x)}
        y={Math.max(0, y - 52)}
        width={Math.min(viewBox.width - 4, 200)}
        height={48}
        fill="#0f172a"
        opacity={0.92}
        rx={3}
      />
      <text
        x={Math.max(0, x) + 6}
        y={Math.max(12, y - 38)}
        fill="#fff"
        fontSize="11"
        fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
      >
        {String(target.type || 'TARGET').toUpperCase()} · {conf}%
      </text>
      <text
        x={Math.max(0, x) + 6}
        y={Math.max(26, y - 24)}
        fill="#cbd5e1"
        fontSize="10"
        fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
      >
        {sourceLabel(target.source)} · {strategy}
      </text>
      <text
        x={Math.max(0, x) + 6}
        y={Math.max(40, y - 10)}
        fill={execStatus === 'success' ? '#86efac' : '#e2e8f0'}
        fontSize="9"
        fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
      >
        {execStatus ? `Result: ${String(execStatus).toUpperCase()}` : 'Selected target'}
      </text>
      <circle cx={cx} cy={cy} r={7} fill="#ef4444" stroke="#fff" strokeWidth={2} />
      <circle cx={cx} cy={cy} r={2.5} fill="#fff" />
    </svg>
  );
}

function LayoutGroupList({ groups }) {
  if (!groups || !groups.length) {
    return <p className="text-sm text-slate-500">No layout groups detected.</p>;
  }
  return (
    <ul className="space-y-2 text-sm max-h-48 overflow-auto">
      {groups.map((g) => (
        <li key={g.id} className="border border-slate-200 rounded px-2 py-1">
          <span className="font-medium text-slate-800">{g.id}</span>
          <span className="text-slate-500"> · {g.type}</span>
          <div className="text-xs text-slate-500 truncate">
            {(g.elements || []).join(', ')}
          </div>
        </li>
      ))}
    </ul>
  );
}

export default function ScreenPreview() {
  const [image, setImage] = useState(null);
  const [sanitized, setSanitized] = useState(false);
  const [error, setError] = useState(null);
  const [uiMap, setUiMap] = useState(null);
  const [actionTarget, setActionTarget] = useState(null);
  const [view, setView] = useState('fused');
  const [naturalSize, setNaturalSize] = useState({ width: 0, height: 0 });
  const [viewBox, setViewBox] = useState({ width: 640, height: 360 });

  useEffect(() => {
    const fetchScreenshot = () => {
      fetch('/api/agent/screenshot')
        .then((res) => res.json())
        .then((data) => {
          setImage(data.image);
          setSanitized(Boolean(data.sanitized));
          setUiMap(data.visual_ui_map || null);
          setActionTarget(data.action_target || null);
        })
        .catch((err) => setError(err.message));
    };

    fetchScreenshot();
    const interval = setInterval(fetchScreenshot, 3000);
    return () => clearInterval(interval);
  }, []);

  const overlayElements = useMemo(() => {
    if (!uiMap) return [];
    if (view === 'dom') return uiMap.views?.dom_elements || [];
    if (view === 'vision') return uiMap.views?.vision_elements || [];
    if (view === 'fused' || view === 'layout') {
      return uiMap.views?.fused_elements || uiMap.elements || [];
    }
    if (view === 'target' || view === 'action') {
      // Base map + highlight handled separately
      return uiMap.views?.fused_elements || uiMap.elements || [];
    }
    return [];
  }, [uiMap, view]);

  const summary = uiMap?.summary || {};
  const dims = uiMap?.dimensions || {};
  const showMapOverlay =
    view !== 'screenshot' && view !== 'layout' && view !== 'target' && view !== 'action';
  const showTargetOverlay = view === 'target' || view === 'action';

  const natural = {
    width: naturalSize.width || dims.width || 1,
    height: naturalSize.height || dims.height || 1,
  };

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
        <h2 className="font-semibold">Visual Perception Map</h2>
        {image && (
          <span
            className={`text-xs px-2 py-0.5 rounded ${
              sanitized
                ? 'bg-green-100 text-green-800'
                : 'bg-amber-100 text-amber-800'
            }`}
          >
            {sanitized ? 'Sanitized (server)' : 'Unmarked'}
            {uiMap?.privacy_safe ? ' · UI Map SAFE' : ''}
          </span>
        )}
      </div>

      <p className="text-xs text-slate-500 mb-3">
        Overlay uses the sanitized screenshot only. Sensitive regions stay redacted;
        raw captures never leave the extension.
      </p>

      <div className="flex flex-wrap gap-1 mb-3">
        {VIEWS.map((v) => (
          <button
            key={v.id}
            type="button"
            onClick={() => setView(v.id)}
            className={`text-xs px-2.5 py-1 rounded border transition-colors ${
              view === v.id
                ? 'bg-slate-800 text-white border-slate-800'
                : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-50'
            }`}
          >
            {v.label}
          </button>
        ))}
      </div>

      <div
        className="relative aspect-video bg-slate-100 flex items-center justify-center text-slate-400 overflow-hidden rounded-lg"
        ref={(node) => {
          if (node) {
            const rect = node.getBoundingClientRect();
            if (
              Math.abs(rect.width - viewBox.width) > 1 ||
              Math.abs(rect.height - viewBox.height) > 1
            ) {
              setViewBox({ width: rect.width, height: rect.height });
            }
          }
        }}
      >
        {error && <p className="text-red-500 text-sm">Error: {error}</p>}
        {!error && image && (
          <>
            <img
              src={image}
              alt="Sanitized screen preview"
              className="w-full h-full object-contain"
              onLoad={(e) => {
                setNaturalSize({
                  width: dims.width || e.target.naturalWidth || 1,
                  height: dims.height || e.target.naturalHeight || 1,
                });
              }}
            />
            {showMapOverlay && (
              <OverlayBoxes
                elements={overlayElements}
                naturalSize={natural}
                viewBox={viewBox}
              />
            )}
            {showTargetOverlay && (
              <>
                <OverlayBoxes
                  elements={overlayElements}
                  naturalSize={natural}
                  viewBox={viewBox}
                />
                <ActionTargetOverlay
                  target={actionTarget}
                  naturalSize={natural}
                  viewBox={viewBox}
                />
              </>
            )}
            {view === 'layout' && (
              <div className="absolute inset-0 bg-slate-900/70 text-white p-4 overflow-auto">
                <h3 className="text-sm font-semibold mb-2 tracking-wide">
                  LAYOUT GROUPS
                </h3>
                <LayoutGroupList groups={uiMap?.layout?.groups} />
                {overlayElements.length > 0 && (
                  <div className="mt-3 relative min-h-[120px]">
                    <OverlayBoxes
                      elements={overlayElements}
                      naturalSize={natural}
                      viewBox={{ width: viewBox.width - 32, height: 160 }}
                    />
                  </div>
                )}
              </div>
            )}
          </>
        )}
        {!error && !image && <p>No active session</p>}
      </div>

      {showTargetOverlay && (
        <div className="mt-3 text-xs text-slate-600 border border-slate-200 rounded-lg p-3">
          {actionTarget ? (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <div>
                Strategy:{' '}
                <span className="font-medium">
                  {String(actionTarget.strategy || '—').toUpperCase()}
                </span>
              </div>
              <div>
                Source:{' '}
                <span className="font-medium">
                  {sourceLabel(actionTarget.source) || '—'}
                </span>
              </div>
              <div>
                Confidence:{' '}
                <span className="font-medium">
                  {actionTarget.confidence != null
                    ? `${Math.round(actionTarget.confidence * 100)}%`
                    : '—'}
                </span>
              </div>
              <div>
                Result:{' '}
                <span className="font-medium">
                  {actionTarget.execution?.status || actionTarget.status || 'pending'}
                </span>
              </div>
            </div>
          ) : (
            <p className="text-slate-500">No action target selected yet.</p>
          )}
        </div>
      )}

      {uiMap && (
        <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs text-slate-600">
          <div>Elements: {summary.total_elements ?? 0}</div>
          <div>Fused: {summary.fused_elements ?? 0}</div>
          <div>DOM only: {summary.dom_only_elements ?? 0}</div>
          <div>Vision only: {summary.vision_only_elements ?? 0}</div>
          {uiMap.performance && (
            <div className="col-span-2 sm:col-span-4 text-slate-500">
              Perception{' '}
              {uiMap.performance.total_visual_perception_ms ?? '—'}
              ms (vision {uiMap.performance.vision_detection_ms ?? '—'}
              ms · fusion {uiMap.performance.fusion_ms ?? '—'}ms)
            </div>
          )}
        </div>
      )}
    </div>
  );
}
