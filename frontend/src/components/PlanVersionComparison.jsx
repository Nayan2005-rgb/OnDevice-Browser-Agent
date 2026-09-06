import React from 'react';

export default function PlanVersionComparison({ preview }) {
  if (!preview) return null;
  const oldPending = preview.old_pending_steps || [];
  const newPending = preview.new_pending_steps || [];
  const completed = preview.completed_steps || [];

  return (
    <div className="grid gap-3 md:grid-cols-2">
      <div>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Current Plan · v{preview.plan_version}
        </h3>
        <ul className="space-y-1">
          {completed.map((s) => (
            <li key={`c-${s.step_id || s.index}`} className="text-sm text-slate-500">
              ✓ {s.description}
            </li>
          ))}
          {oldPending.map((s) => (
            <li key={`o-${s.step_id || s.index}`} className="text-sm text-slate-800">
              → {s.description}
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-teal-700">
          Proposed Plan · v{preview.proposed_version}
        </h3>
        <ul className="space-y-1">
          {completed.map((s) => (
            <li key={`pc-${s.step_id || s.index}`} className="text-sm text-slate-500">
              ✓ {s.description}
            </li>
          ))}
          {newPending.map((s) => {
            const isAdd = (preview.added_steps || []).includes(s.description);
            return (
              <li
                key={`n-${s.step_id || s.index}`}
                className={`text-sm ${isAdd ? 'font-medium text-teal-800' : 'text-slate-800'}`}
              >
                {isAdd ? '+ ' : '→ '}
                {s.description}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
