import React, { useState } from 'react';
import PlanVersionComparison from './PlanVersionComparison.jsx';

export default function ReplanPreview({ sessionId, preview, onResolved }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);

  if (!preview || !sessionId) return null;

  const act = async (path) => {
    setBusy(path);
    setError(null);
    try {
      const res = await fetch(
        `/api/agent/sessions/${encodeURIComponent(sessionId)}/${path}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        }
      );
      const data = await res.json();
      if (!res.ok) setError(data.error || data.status || 'Failed');
      if (onResolved) onResolved(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 shadow-sm md:col-span-2">
      <h2 className="mb-1 font-semibold text-amber-950">Page Structure Changed</h2>
      <p className="mb-3 text-sm text-amber-900">
        Change level:{' '}
        <span className="font-semibold uppercase">
          {preview.page_change_level || 'unknown'}
        </span>
        {preview.reason ? (
          <>
            {' '}
            · Reason: {String(preview.reason).replace(/_/g, ' ')}
          </>
        ) : null}
      </p>
      <PlanVersionComparison preview={preview} />
      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          disabled={!!busy}
          onClick={() => act('approve-replan')}
          className="rounded-lg bg-teal-700 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
        >
          {busy === 'approve-replan' ? 'Approving…' : 'Approve Replan'}
        </button>
        <button
          type="button"
          disabled={!!busy}
          onClick={() => act('reject-replan')}
          className="rounded-lg border border-amber-800 bg-white px-3 py-1.5 text-sm font-medium text-amber-950 disabled:opacity-40"
        >
          {busy === 'reject-replan' ? 'Rejecting…' : 'Reject & Pause'}
        </button>
      </div>
      {error ? <p className="mt-2 text-xs text-red-600">{error}</p> : null}
    </div>
  );
}
