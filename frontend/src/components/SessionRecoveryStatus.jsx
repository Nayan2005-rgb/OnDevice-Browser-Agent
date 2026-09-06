import React from 'react';

const STEPS = [
  { key: 'waiting_for_browser', label: 'Waiting for Browser' },
  { key: 'recovering', label: 'Restoring Session' },
  { key: 'validating', label: 'Validating Page State' },
  { key: 'ready', label: 'Session Ready' },
  { key: 'replanning', label: 'Replanning' },
  { key: 'requires_user_intervention', label: 'Needs User' },
  { key: 'failed', label: 'Recovery Failed' },
];

export default function SessionRecoveryStatus({ session }) {
  const status = (session && session.recovery_status) || null;
  const sessionStatus = session && session.status;

  const show =
    status ||
    ['waiting_for_browser', 'recovering'].includes(sessionStatus);

  if (!show) {
    return (
      <div className="rounded-xl border p-4 shadow-sm">
        <h2 className="mb-2 font-semibold">Recovery</h2>
        <p className="text-sm text-emerald-700">Ready</p>
      </div>
    );
  }

  const active = status || sessionStatus || 'waiting_for_browser';
  const activeIdx = Math.max(
    0,
    STEPS.findIndex((s) => s.key === active)
  );

  return (
    <div className="rounded-xl border border-indigo-200 bg-indigo-50/60 p-4 shadow-sm">
      <h2 className="mb-1 font-semibold text-indigo-950">Session Recovery</h2>
      <p className="mb-3 text-xs text-indigo-800">
        Server restart or reconnect detected — fresh perception required before any action.
      </p>
      <ol className="space-y-2">
        {STEPS.slice(0, 4).map((step, i) => {
          const done = i < activeIdx || active === 'ready';
          const current = step.key === active;
          return (
            <li
              key={step.key}
              className={`flex items-center gap-2 text-sm ${
                current
                  ? 'font-semibold text-indigo-900'
                  : done
                    ? 'text-indigo-700'
                    : 'text-indigo-400'
              }`}
            >
              <span>{done || current ? '✓' : '○'}</span>
              {step.label}
            </li>
          );
        })}
      </ol>
      {['replanning', 'requires_user_intervention', 'failed'].includes(active) ? (
        <p className="mt-3 text-xs font-medium uppercase tracking-wide text-indigo-800">
          {STEPS.find((s) => s.key === active)?.label}
        </p>
      ) : null}
    </div>
  );
}
