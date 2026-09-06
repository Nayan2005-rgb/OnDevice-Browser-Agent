import React, { useEffect, useState } from 'react';

export default function AgentStatus() {
  const [status, setStatus] = useState('Loading...');
  const [lastTask, setLastTask] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const load = () => {
      fetch('/api/agent/status')
        .then((res) => res.json())
        .then((data) => {
          setStatus(data.status);
          setLastTask(data.last_task || null);
        })
        .catch((err) => setError(err.message));
    };
    load();
    const id = setInterval(load, 3000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <h2 className="font-semibold mb-2">Agent Status</h2>
      <p className="text-sm text-slate-500">
        {error ? `Error: ${error}` : status}
      </p>
      {lastTask && (
        <p className="text-xs text-slate-400 mt-1 truncate">Task: {lastTask}</p>
      )}
    </div>
  );
}