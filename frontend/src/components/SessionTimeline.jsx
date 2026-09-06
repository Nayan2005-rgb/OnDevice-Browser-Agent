import React from 'react';

const LABELS = {
  session_created: 'Session Created',
  plan_created: 'Plan Created',
  step_started: 'Step Started',
  step_completed: 'Step Completed',
  step_failed: 'Step Failed',
  page_changed: 'Page Changed',
  replan_created: 'Replan Created',
  replan_approved: 'Replan Approved',
  replan_rejected: 'Replan Rejected',
  confirmation_requested: 'Confirmation Requested',
  confirmation_approved: 'Confirmation Approved',
  confirmation_cancelled: 'Confirmation Cancelled',
  paused: 'Paused',
  resumed: 'Resumed',
  recovery_started: 'Recovery Started',
  recovery_completed: 'Recovery Completed',
  user_intervention_required: 'Waiting for User',
  session_completed: 'Session Completed',
  session_failed: 'Session Failed',
  session_cancelled: 'Session Cancelled',
};

export default function SessionTimeline({ events }) {
  const items = Array.isArray(events) ? events : [];

  return (
    <div className="rounded-xl border p-4 shadow-sm md:col-span-2">
      <h2 className="mb-3 font-semibold">Agent Execution Timeline</h2>
      {items.length === 0 ? (
        <p className="text-sm text-slate-500">No session events yet.</p>
      ) : (
        <ol className="relative ml-2 border-l border-slate-200 pl-4">
          {items.map((ev, idx) => {
            const last = idx === items.length - 1;
            const label = LABELS[ev.event_type] || String(ev.event_type || '').replace(/_/g, ' ');
            const meta = ev.safe_metadata || {};
            return (
              <li key={ev.event_id || `${ev.event_type}-${idx}`} className="mb-4 last:mb-0">
                <span
                  className={`absolute -left-1.5 mt-1.5 h-3 w-3 rounded-full border-2 border-white ${
                    last ? 'bg-teal-600' : 'bg-slate-400'
                  }`}
                />
                <div className="text-sm font-medium text-slate-800">
                  {last ? '◉' : '●'} {label}
                  {ev.plan_version != null ? (
                    <span className="ml-2 text-xs font-normal text-slate-500">
                      v{ev.plan_version}
                    </span>
                  ) : null}
                </div>
                {meta.reason ? (
                  <p className="mt-0.5 text-xs text-slate-500">
                    {String(meta.reason).replace(/_/g, ' ')}
                  </p>
                ) : null}
                {meta.page_change_level ? (
                  <p className="text-xs uppercase tracking-wide text-slate-400">
                    {meta.page_change_level}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
