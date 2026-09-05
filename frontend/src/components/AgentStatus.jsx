import React, { useEffect, useState } from 'react';

export default function AgentStatus() {
  const [status, setStatus] = useState('Loading...');
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch('/api/agent/status')
      .then((res) => res.json())
      .then((data) => setStatus(data.status))
      .catch((err) => setError(err.message));
  }, []);

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <h2 className="font-semibold mb-2">Agent Status</h2>
      <p className="text-sm text-slate-500">
        {error ? `Error: ${error}` : status}
      </p>
    </div>
  );
}