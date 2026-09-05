import React, { useEffect, useState } from 'react';

export default function ActionHistory() {
  const [actions, setActions] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch('/api/agent/history')
      .then((res) => res.json())
      .then((data) => setActions(data.actions || []))
      .catch((err) => setError(err.message));
  }, []);

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <h2 className="font-semibold mb-2">Action History</h2>
      {error && <p className="text-sm text-red-500">Error: {error}</p>}
      {actions.length === 0 ? (
        <p className="text-sm text-slate-500">No actions recorded yet.</p>
      ) : (
        <ul className="text-sm space-y-1">
          {actions.map((a, i) => (
            <li key={i}>{JSON.stringify(a)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}