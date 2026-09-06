import React, { useEffect, useState } from 'react';

const API = '/api/agent';

export default function PendingConfirmations() {
  const [items, setItems] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    const load = () => {
      fetch(`${API}/actions/pending`)
        .then((r) => r.json())
        .then((data) => setItems(data.confirmations || []))
        .catch((err) => setError(err.message));
    };
    load();
    const id = setInterval(load, 2000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <h2 className="mb-2 font-semibold">Pending Confirmations</h2>
      <p className="mb-3 text-xs text-stone-500">
        Durable confirmations restored after restart — approve only once.
      </p>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {!error && items.length === 0 && (
        <p className="text-sm text-stone-500">No pending confirmations.</p>
      )}
      <ul className="space-y-2">
        {items.map((c) => (
          <li
            key={c.id}
            className="rounded-lg border border-amber-200 bg-amber-50/70 px-3 py-2 text-sm"
          >
            <div className="font-medium text-amber-950">
              {c.task || c.action || 'Action'}
            </div>
            <div className="mt-1 text-xs text-amber-800">
              {c.category} · expires in {c.expires_in_seconds}s
              {c.tab_id != null ? ` · tab ${c.tab_id}` : ''}
            </div>
            <div className="mt-1 text-xs text-stone-600">{c.reason}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}
