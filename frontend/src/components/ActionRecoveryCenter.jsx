import React, { useEffect, useState } from 'react';
import DurableActionTimeline from './DurableActionTimeline.jsx';
import PendingConfirmations from './PendingConfirmations.jsx';
import RecoveryRequiredPanel from './RecoveryRequiredPanel.jsx';

const API = '/api/agent';

export default function ActionRecoveryCenter() {
  const [actions, setActions] = useState([]);
  const [pendingApproved, setPendingApproved] = useState([]);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState(null);

  const load = () => {
    Promise.all([
      fetch(`${API}/actions/recovery`).then((r) => r.json()),
      fetch(`${API}/actions/pending`).then((r) => r.json()),
    ])
      .then(([rec, pend]) => {
        const list = rec.actions || [];
        setActions(list);
        setPendingApproved(pend.approved_actions || []);
        setSelected((prev) => {
          if (prev && list.find((a) => a.execution_id === prev.execution_id)) {
            return prev;
          }
          return list[0] || null;
        });
      })
      .catch((err) => setError(err.message));
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 2500);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="rounded-xl border border-stone-200 bg-gradient-to-br from-stone-50 to-teal-50/40 p-4 shadow-sm md:col-span-2">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="font-semibold text-stone-900">Action Recovery Center</h2>
          <p className="text-xs text-stone-500">
            Durable confirmations, leases, and crash-safe recovery — never blind replay.
          </p>
        </div>
        <span className="rounded-md border border-teal-200 bg-white px-2 py-1 text-xs text-teal-800">
          {actions.length} recovery · {pendingApproved.length} waiting
        </span>
      </div>
      {error && <p className="mb-2 text-sm text-red-600">{error}</p>}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <div className="space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-stone-500">
            Interrupted
          </h3>
          {actions.length === 0 && (
            <p className="text-sm text-stone-500">None</p>
          )}
          <ul className="space-y-1.5">
            {actions.map((a) => (
              <li key={a.execution_id || a.lifecycle_id}>
                <button
                  type="button"
                  onClick={() => setSelected(a)}
                  className={`w-full rounded-lg border px-2 py-2 text-left text-sm ${
                    selected &&
                    selected.execution_id === a.execution_id
                      ? 'border-teal-600 bg-white'
                      : 'border-stone-200 bg-white/70'
                  }`}
                >
                  <div className="font-medium">{a.task || a.action_type}</div>
                  <div className="text-xs text-stone-500">
                    {a.status}
                    {a.recovery_reason ? ` · ${a.recovery_reason}` : ''}
                  </div>
                </button>
              </li>
            ))}
          </ul>
          <PendingConfirmations />
        </div>
        <RecoveryRequiredPanel action={selected} onUpdated={load} />
        <DurableActionTimeline
          lifecycleId={selected?.lifecycle_id || undefined}
        />
      </div>
    </div>
  );
}
