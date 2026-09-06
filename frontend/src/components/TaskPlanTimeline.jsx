import React from 'react';

const STEP_MARK = {
  success: '✓',
  failed: '✗',
  cancelled: '■',
  skipped: '○',
  pending: '○',
  ready: '○',
  resolving: '◉',
  executing: '◉',
  verifying: '◉',
  waiting: '⏳',
  requires_confirmation: '⚠',
  approved: '⏳',
  recovering: '↻',
};

function stepMark(status) {
  return STEP_MARK[status] || '○';
}

function sourceLine(step) {
  const parts = [];
  if (step.resolve_strategy) parts.push(String(step.resolve_strategy).replace(/_/g, ' '));
  if (step.resolve_source) parts.push(String(step.resolve_source));
  if (step.verification_status === 'success') parts.push('Verified');
  if (step.status === 'requires_confirmation') parts.push('Needs confirmation');
  if (step.status === 'waiting') parts.push('Waiting');
  if (step.status === 'recovering') parts.push('Recovering');
  if (step.status === 'failed' && step.error) parts.push(step.error);
  return parts.length ? parts.join(' • ') : step.action_type || '';
}

export default function TaskPlanTimeline({ plan }) {
  if (!plan) {
    return (
      <div className="rounded-xl border p-4 shadow-sm">
        <h2 className="mb-2 font-semibold">Task Plan</h2>
        <p className="text-sm text-slate-500">No active multi-step plan.</p>
      </div>
    );
  }

  const steps = plan.steps || [];
  const currentIndex = plan.current_step_index;
  const version = plan.version ?? plan.plan_version ?? 1;
  const pageChange = plan.page_change;
  const changeLevel = (pageChange && pageChange.level) || plan.last_page_change_level;

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-semibold">Task Plan</h2>
        <span className="text-xs font-medium text-slate-500">Version {version}</span>
      </div>
      <p className="mb-3 text-xs uppercase tracking-wide text-slate-500">User Goal</p>
      <p className="mb-4 text-sm font-medium text-slate-800">{plan.goal}</p>

      {plan.requires_user_intervention ? (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950">
          <div className="font-semibold">⏸ Waiting for user action</div>
          <div className="mt-1 text-xs">
            {plan.intervention_reason || 'Sensitive requirement detected.'}
          </div>
        </div>
      ) : null}

      {changeLevel && changeLevel !== 'none' ? (
        <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-800">
          <div className="font-semibold">
            ⚠ Page changed — {String(changeLevel).toUpperCase()}
          </div>
          {(pageChange?.reasons || []).length ? (
            <div className="mt-1 text-xs text-slate-600">
              {(pageChange.reasons || []).slice(0, 3).join(', ').replace(/_/g, ' ')}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="mb-2 border-t border-slate-200" />
      <ol className="space-y-3">
        {steps.map((step, i) => {
          const active =
            i === currentIndex &&
            !['success', 'failed', 'cancelled', 'skipped'].includes(step.status);
          return (
            <li
              key={step.step_id || i}
              className={`flex gap-3 rounded-lg px-2 py-1.5 ${
                active ? 'bg-teal-50 ring-1 ring-teal-200' : ''
              }`}
            >
              <span
                className={`mt-0.5 w-5 shrink-0 text-center text-base ${
                  step.status === 'failed'
                    ? 'text-red-600'
                    : step.status === 'success'
                      ? 'text-teal-700'
                      : active
                        ? 'text-teal-800'
                        : 'text-slate-400'
                }`}
              >
                {stepMark(step.status)}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-slate-800">
                  {step.index || i + 1}. {step.description}
                  {active ? (
                    <span className="ml-2 text-xs font-normal text-teal-700">
                      Current step
                    </span>
                  ) : null}
                </div>
                <div className="text-xs capitalize text-slate-500">{sourceLine(step)}</div>
              </div>
            </li>
          );
        })}
      </ol>
      <p className="mt-3 text-xs text-slate-500">
        Status: <span className="font-medium text-slate-700">{plan.status}</span>
        {typeof plan.replan_count === 'number' ? (
          <span className="ml-3">
            Replans: {plan.replan_count}/{plan.metrics?.max_replan_attempts ?? 2}
          </span>
        ) : null}
      </p>
    </div>
  );
}
