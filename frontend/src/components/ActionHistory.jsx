import React, { useEffect, useState } from 'react';

function sourceLabel(source) {
  if (!source) return null;
  if (source === 'dom+vision') return 'DOM + Vision';
  if (source === 'dom') return 'DOM';
  if (source === 'vision') return 'Vision';
  return String(source);
}

function strategyLabel(strategy, action) {
  if (strategy === 'coordinates' || action === 'coordinate_click') {
    return 'Visual Coordinates';
  }
  if (strategy === 'selector' || action === 'click' || action === 'type') {
    return 'DOM Selector';
  }
  if (action === 'scroll') return 'Scroll';
  return strategy || null;
}

function actionLabel(action) {
  if (action === 'coordinate_click') return 'Click';
  if (!action) return 'none';
  return String(action).replace(/_/g, ' ');
}

export default function ActionHistory() {
  const [actions, setActions] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    const load = () => {
      fetch('/api/agent/history')
        .then((res) => res.json())
        .then((data) => setActions(data.actions || []))
        .catch((err) => setError(err.message));
    };
    load();
    const id = setInterval(load, 3000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <h2 className="font-semibold mb-2">Action History</h2>
      {error && <p className="text-sm text-red-500">Error: {error}</p>}
      {actions.length === 0 ? (
        <p className="text-sm text-slate-500">No actions recorded yet.</p>
      ) : (
        <ul className="text-sm space-y-2">
          {actions.map((a, i) => {
            const strategy = strategyLabel(a.strategy, a.action);
            const source = sourceLabel(a.source);
            const conf =
              a.confidence != null ? Math.round(Number(a.confidence) * 100) : null;
            const execStatus = a.execution?.status;
            return (
              <li key={i} className="border-b border-slate-100 pb-2">
                <div className="font-medium">
                  Action: {actionLabel(a.action)} — {a.status}
                </div>
                <div className="text-xs text-slate-500 truncate">{a.task}</div>
                {strategy && (
                  <div className="text-xs text-slate-500">Strategy: {strategy}</div>
                )}
                {source && (
                  <div className="text-xs text-slate-500">Source: {source}</div>
                )}
                {conf != null && (
                  <div className="text-xs text-slate-500">Confidence: {conf}%</div>
                )}
                {execStatus && (
                  <div className="text-xs text-slate-500">
                    Result: {String(execStatus)}
                  </div>
                )}
                {a.safety?.level && (
                  <div className="text-xs text-slate-500">
                    Safety: {a.safety.level}
                    {a.safety.category ? ` (${a.safety.category})` : ''}
                  </div>
                )}
                {a.confirmation?.required && (
                  <div className="text-xs text-slate-500">
                    Confirmation:{' '}
                    {a.confirmation.approved
                      ? a.status === 'waiting_for_extension'
                        ? 'approved — waiting for browser'
                        : 'approved'
                      : 'required'}
                  </div>
                )}
                {a.lifecycle_state && (
                  <div className="text-xs text-slate-500">
                    Lifecycle: {a.lifecycle_state}
                  </div>
                )}
                {a.verification?.status && (
                  <div className="text-xs text-slate-500">
                    Verification: {a.verification.status}
                  </div>
                )}
                {a.recovery_attempts != null && a.recovery_attempts > 0 && (
                  <div className="text-xs text-slate-500">
                    Recovery attempts: {a.recovery_attempts}
                  </div>
                )}
                {a.privacy_redactions != null && (
                  <div className="text-xs text-slate-400">
                    redactions: {a.privacy_redactions}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
