import React, { useEffect, useState } from 'react';

const API = '/api/agent';

const FLOW = [
  'created',
  'requires_confirmation',
  'approved',
  'waiting_for_extension',
  'claimed',
  'executing',
  'verifying',
  'success',
];

const LABELS = {
  created: 'Created',
  requires_confirmation: 'Waiting for Confirmation',
  pending_confirmation: 'Waiting for Confirmation',
  approved: 'Approved',
  waiting_for_extension: 'Waiting for Browser',
  waiting_for_browser: 'Waiting for Browser',
  claimed: 'Claimed',
  executing: 'Executing',
  verifying: 'Verification',
  verification_pending: 'Verification',
  success: 'Success',
  recovery_required: 'Recovery Required',
  failed: 'Failed',
  cancelled: 'Cancelled',
  expired: 'Expired',
};

export default function DurableActionTimeline({ lifecycleId }) {
  const [lifecycle, setLifecycle] = useState(null);
  const [timeline, setTimeline] = useState([]);

  useEffect(() => {
    const load = () => {
      const q = lifecycleId ? `?id=${encodeURIComponent(lifecycleId)}` : '';
      fetch(`${API}/lifecycle${q}`)
        .then((r) => r.json())
        .then((data) => {
          const life = data.lifecycle || null;
          setLifecycle(life);
          if (life?.lifecycle_id) {
            fetch(`${API}/action/${encodeURIComponent(life.lifecycle_id)}/timeline`)
              .then((r) => r.json())
              .then((t) => setTimeline(t.timeline || []))
              .catch(() => setTimeline(life.timeline || []));
          }
        })
        .catch(() => {});
    };
    load();
    const id = setInterval(load, 2000);
    return () => clearInterval(id);
  }, [lifecycleId]);

  if (!lifecycle) {
    return (
      <div className="rounded-xl border p-4 shadow-sm">
        <h2 className="mb-2 font-semibold">Durable Action Timeline</h2>
        <p className="text-sm text-stone-500">No active durable action.</p>
      </div>
    );
  }

  const current = lifecycle.state;
  const seen = new Set(
    (lifecycle.timeline || []).map((t) => t.state).concat([current])
  );
  const isRecovery = current === 'recovery_required';

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <h2 className="mb-1 font-semibold">Durable Action Timeline</h2>
      <p className="mb-3 text-xs text-stone-500">
        {lifecycle.task || 'Action'} · {LABELS[current] || current}
      </p>
      <div className="mb-3 grid grid-cols-2 gap-2 text-xs text-stone-600">
        <div>Session: {lifecycle.session_id || '—'}</div>
        <div>Plan: {lifecycle.plan_id || '—'}</div>
        <div>Step: {lifecycle.step_id || '—'}</div>
        <div>Risk: {lifecycle.risk_level || lifecycle.safety?.level || '—'}</div>
        <div>Claim: {lifecycle.execution_status || '—'}</div>
        <div>Verify: {lifecycle.verification?.status || '—'}</div>
      </div>
      <ol className="space-y-1.5">
        {(isRecovery
          ? [...FLOW.slice(0, 6), 'recovery_required']
          : FLOW
        ).map((state) => {
          const done = seen.has(state) || state === current;
          const active = state === current;
          return (
            <li
              key={state}
              className={`flex items-center gap-2 text-sm ${
                active
                  ? 'font-semibold text-teal-900'
                  : done
                    ? 'text-teal-700'
                    : 'text-stone-400'
              }`}
            >
              <span>{active ? '●' : done ? '✓' : '○'}</span>
              {LABELS[state] || state}
            </li>
          );
        })}
      </ol>
      {lifecycle.recovery_reason && (
        <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-2 py-1.5 text-xs text-amber-900">
          Recovery: {lifecycle.recovery_reason}
        </p>
      )}
      {timeline.length > 0 && (
        <details className="mt-3 text-xs text-stone-500">
          <summary className="cursor-pointer">Event log ({timeline.length})</summary>
          <ul className="mt-1 max-h-32 space-y-1 overflow-auto">
            {timeline.map((e, i) => (
              <li key={e.event_id || i}>
                {e.to_state || e.state}
                {e.reason ? ` — ${e.reason}` : ''}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
