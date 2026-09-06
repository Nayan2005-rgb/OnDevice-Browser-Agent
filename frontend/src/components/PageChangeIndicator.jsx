import React from 'react';

const LEVEL_STYLES = {
  none: 'bg-slate-100 text-slate-600',
  minor: 'bg-sky-50 text-sky-800 ring-sky-200',
  moderate: 'bg-amber-50 text-amber-900 ring-amber-200',
  structural: 'bg-orange-50 text-orange-900 ring-orange-300',
  navigation: 'bg-indigo-50 text-indigo-900 ring-indigo-200',
};

export default function PageChangeIndicator({ plan }) {
  const change = (plan && plan.page_change) || null;
  const level = (change && change.level) || plan?.last_page_change_level || 'none';
  const style = LEVEL_STYLES[level] || LEVEL_STYLES.none;
  const reasons = (change && change.reasons) || [];
  const replanCount = plan?.replan_count ?? 0;
  const maxReplans = plan?.metrics?.max_replan_attempts ?? 2;

  if (!plan) {
    return (
      <div className="rounded-xl border p-4 shadow-sm">
        <h2 className="mb-2 font-semibold">Page Change</h2>
        <p className="text-sm text-slate-500">No active plan.</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <h2 className="mb-3 font-semibold">Page Change</h2>
      <div
        className={`mb-3 inline-flex items-center rounded-md px-3 py-1.5 text-xs font-bold uppercase tracking-wide ring-1 ${style}`}
      >
        {String(level).toUpperCase()}
      </div>
      {change && typeof change.score === 'number' ? (
        <p className="mb-2 text-xs text-slate-500">
          Score: {Number(change.score).toFixed(2)}
        </p>
      ) : null}
      {reasons.length ? (
        <ul className="mb-3 list-inside list-disc text-xs text-slate-600">
          {reasons.slice(0, 5).map((r) => (
            <li key={r}>{String(r).replace(/_/g, ' ')}</li>
          ))}
        </ul>
      ) : (
        <p className="mb-3 text-xs text-slate-500">No significant change detected.</p>
      )}
      <div className="border-t border-slate-100 pt-3">
        <p className="text-xs font-medium text-slate-700">Replan Attempts</p>
        <p className="mt-1 text-sm font-semibold text-slate-900">
          {replanCount} / {maxReplans}
        </p>
        {plan.last_replan_reason ? (
          <p className="mt-1 text-xs text-slate-500">
            Last: {String(plan.last_replan_reason).replace(/_/g, ' ')}
          </p>
        ) : null}
      </div>
    </div>
  );
}
