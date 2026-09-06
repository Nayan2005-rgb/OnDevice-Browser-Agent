import React from 'react';

export default function UserInterventionPanel({ plan }) {
  const needed = Boolean(plan && plan.requires_user_intervention);
  const reason =
    (plan && plan.intervention_reason) ||
    'A sensitive page requirement was detected.';

  if (!plan || !needed) {
    return (
      <div className="rounded-xl border p-4 shadow-sm">
        <h2 className="mb-2 font-semibold">User Action</h2>
        <p className="text-sm text-slate-500">No user intervention required.</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-amber-300 bg-amber-50 p-4 shadow-sm">
      <h2 className="mb-2 font-semibold text-amber-950">⚠ User action required</h2>
      <p className="mb-3 text-sm text-amber-900">{reason}</p>
      <p className="text-xs leading-relaxed text-amber-800">
        The agent will not enter passwords, OTP codes, payment data, or solve CAPTCHAs
        automatically. Complete the requirement in the browser, then resume the task plan.
        Resume always uses fresh perception.
      </p>
      <p className="mt-3 text-xs font-medium uppercase tracking-wide text-amber-700">
        Status: {plan.status}
      </p>
    </div>
  );
}
