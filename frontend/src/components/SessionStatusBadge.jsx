import React from 'react';

const STATUS_STYLES = {
  running: 'bg-emerald-100 text-emerald-800 border-emerald-200',
  paused: 'bg-amber-100 text-amber-900 border-amber-200',
  waiting_for_confirmation: 'bg-sky-100 text-sky-900 border-sky-200',
  waiting_for_browser: 'bg-slate-100 text-slate-800 border-slate-200',
  requires_user_intervention: 'bg-orange-100 text-orange-900 border-orange-200',
  recovering: 'bg-indigo-100 text-indigo-900 border-indigo-200',
  completed: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  failed: 'bg-red-100 text-red-800 border-red-200',
  cancelled: 'bg-slate-100 text-slate-600 border-slate-200',
  created: 'bg-slate-50 text-slate-700 border-slate-200',
  expired: 'bg-slate-100 text-slate-500 border-slate-200',
};

export default function SessionStatusBadge({ status }) {
  const key = (status || 'created').toLowerCase();
  const style = STATUS_STYLES[key] || STATUS_STYLES.created;
  const label = String(status || 'unknown').replace(/_/g, ' ');
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-semibold uppercase tracking-wide ${style}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-80" />
      {label}
    </span>
  );
}
