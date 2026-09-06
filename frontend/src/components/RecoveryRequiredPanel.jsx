import React, { useEffect, useState } from 'react';

const API = '/api/agent';

export default function RecoveryRequiredPanel({ action, onUpdated }) {
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState(null);

  if (!action) {
    return (
      <div className="rounded-xl border p-4 shadow-sm">
        <h2 className="mb-2 font-semibold">Recovery Required</h2>
        <p className="text-sm text-stone-500">No interrupted actions.</p>
      </div>
    );
  }

  const post = async (path, body) => {
    setBusy(true);
    setFeedback(null);
    try {
      const res = await fetch(`${API}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      setFeedback(data.recovery?.decision || data.status || 'done');
      if (onUpdated) onUpdated(data);
    } catch (err) {
      setFeedback(err.message || 'Request failed');
    } finally {
      setBusy(false);
    }
  };

  const base = {
    execution_id: action.execution_id,
    lifecycle_id: action.lifecycle_id,
    tab_id: action.tab_id,
  };

  return (
    <div className="rounded-xl border border-amber-300 bg-amber-50/50 p-4 shadow-sm">
      <h2 className="mb-1 font-semibold text-amber-950">Recovery Required</h2>
      <p className="mb-2 text-sm text-amber-900">
        Action: {action.task || action.action_type || 'Unknown'}
      </p>
      <dl className="mb-3 grid grid-cols-2 gap-1 text-xs text-amber-900">
        <dt>Status</dt>
        <dd>{action.status}</dd>
        <dt>Reason</dt>
        <dd>{action.recovery_reason || 'Interrupted'}</dd>
        <dt>Last stage</dt>
        <dd>{action.status}</dd>
        <dt>Fresh perception</dt>
        <dd>Required</dd>
        <dt>Page state</dt>
        <dd>Not yet validated</dd>
        <dt>Lease</dt>
        <dd>
          {action.lease_until
            ? new Date(action.lease_until * 1000).toLocaleTimeString()
            : '—'}
        </dd>
      </dl>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={busy}
          className="rounded-lg border border-teal-700 bg-teal-700 px-3 py-1.5 text-xs text-white disabled:opacity-50"
          onClick={() =>
            post('/action/revalidate', {
              ...base,
              // Frontend never sends selectors/coords — backend owns recovery
              page: { url: 'reconnect://pending', title: 'Reconnect', elements: [] },
            })
          }
        >
          Validate Page
        </button>
        <button
          type="button"
          disabled={busy}
          className="rounded-lg border border-amber-700 bg-amber-700 px-3 py-1.5 text-xs text-white disabled:opacity-50"
          onClick={() =>
            post('/action/recover', {
              ...base,
              page: { url: 'reconnect://pending', title: 'Reconnect', elements: [] },
            })
          }
        >
          Require Confirmation
        </button>
        <button
          type="button"
          disabled={busy}
          className="rounded-lg border border-stone-400 bg-white px-3 py-1.5 text-xs text-stone-800 disabled:opacity-50"
          onClick={() => post('/action/cancel', base)}
        >
          Cancel Action
        </button>
      </div>
      {feedback && (
        <p className="mt-2 text-xs text-stone-700">Result: {feedback}</p>
      )}
      <p className="mt-2 text-[11px] text-stone-500">
        Controls use backend-owned state. Selectors and coordinates are never submitted.
      </p>
    </div>
  );
}
