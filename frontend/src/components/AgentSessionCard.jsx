import React from 'react';
import SessionStatusBadge from './SessionStatusBadge.jsx';

export default function AgentSessionCard({ session, selected, onSelect }) {
  if (!session) return null;
  const label = session.session_label || `#${(session.session_id || '').slice(-6).toUpperCase()}`;
  return (
    <button
      type="button"
      onClick={() => onSelect && onSelect(session)}
      className={`w-full rounded-lg border px-3 py-2 text-left transition ${
        selected
          ? 'border-teal-600 bg-teal-50 shadow-sm'
          : 'border-slate-200 bg-white hover:border-slate-300'
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-sm font-semibold text-slate-800">{label}</span>
        <SessionStatusBadge status={session.status} />
      </div>
      <p className="mt-1 truncate text-sm text-slate-600">{session.goal || '—'}</p>
      <p className="mt-1 text-xs text-slate-400">
        Plan v{session.plan_version || 1}
        {session.current_step_index != null
          ? ` · Step ${Number(session.current_step_index) + 1}`
          : ''}
      </p>
    </button>
  );
}
