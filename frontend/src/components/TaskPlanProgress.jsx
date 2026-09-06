import React from 'react';

export default function TaskPlanProgress({ plan }) {
  if (!plan) return null;

  const total = plan.total_steps || (plan.steps || []).length || 0;
  const done = (plan.metrics && plan.metrics.successful_steps) || 0;
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <h2 className="mb-3 font-semibold">Plan Progress</h2>
      <div className="mb-2 h-3 w-full overflow-hidden rounded bg-slate-100">
        <div
          className="h-full rounded bg-teal-600 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-sm text-slate-700">
        {done} / {total} Steps Complete
      </p>
      {plan.metrics ? (
        <ul className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-500">
          <li>Confirmations: {plan.metrics.confirmation_count ?? 0}</li>
          <li>Recoveries: {plan.metrics.recovery_count ?? 0}</li>
          <li>Failed: {plan.metrics.failed_steps ?? 0}</li>
          <li>
            Total:{' '}
            {plan.metrics.plan_total_ms != null
              ? `${Math.round(plan.metrics.plan_total_ms)} ms`
              : '—'}
          </li>
        </ul>
      ) : null}
    </div>
  );
}
