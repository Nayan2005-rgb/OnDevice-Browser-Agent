import React, { useState } from 'react';

export default function SessionControls({ sessionId, status, onUpdated }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  if (!sessionId) {
    return (
      <div className="rounded-xl border p-4 shadow-sm">
        <h2 className="mb-2 font-semibold">Session Controls</h2>
        <p className="text-sm text-slate-500">No active session.</p>
      </div>
    );
  }

  const call = async (action) => {
    setBusy(action);
    setError(null);
    try {
      const res = await fetch(`/api/agent/sessions/${encodeURIComponent(sessionId)}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (!res.ok && data.status === 'fresh_perception_required') {
        setError('Resume needs a fresh browser snapshot from the extension.');
      } else if (!res.ok) {
        setError(data.error || data.status || 'Request failed');
      }
      if (onUpdated) onUpdated(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  };

  const terminal = ['completed', 'failed', 'cancelled', 'expired'].includes(status);

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <h2 className="mb-3 font-semibold">Session Controls</h2>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={!!busy || terminal || status === 'paused'}
          onClick={() => call('pause')}
          className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-800 disabled:opacity-40"
        >
          {busy === 'pause' ? 'Pausing…' : 'Pause'}
        </button>
        <button
          type="button"
          disabled={!!busy || terminal || status === 'cancelled'}
          onClick={() => call('resume')}
          className="rounded-lg border border-teal-700 bg-teal-700 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
        >
          {busy === 'resume' ? 'Resuming…' : 'Resume'}
        </button>
        <button
          type="button"
          disabled={!!busy || status === 'cancelled'}
          onClick={() => call('cancel')}
          className="rounded-lg border border-red-700 bg-red-700 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
        >
          {busy === 'cancel' ? 'Cancelling…' : 'Cancel'}
        </button>
      </div>
      {error ? <p className="mt-2 text-xs text-red-600">{error}</p> : null}
      <p className="mt-3 text-xs text-slate-500">
        Resume always requires fresh sanitized perception — cached coordinates are never replayed.
      </p>
    </div>
  );
}
