import React from 'react';

export default function ReplanHistory({ plan }) {
  const revisions = (plan && plan.revision_history) || [];
  const version = plan?.version ?? plan?.plan_version ?? 1;

  if (!plan) {
    return (
      <div className="rounded-xl border p-4 shadow-sm">
        <h2 className="mb-2 font-semibold">Replan History</h2>
        <p className="text-sm text-slate-500">No revisions yet.</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h2 className="font-semibold">Replan History</h2>
        <span className="text-xs font-medium text-slate-500">Version {version}</span>
      </div>
      {revisions.length === 0 ? (
        <p className="text-sm text-slate-500">Plan has not been replanned.</p>
      ) : (
        <ol className="space-y-3">
          {revisions.map((rev, i) => (
            <li key={`${rev.version}-${i}`} className="rounded-lg bg-slate-50 px-3 py-2">
              <div className="text-xs font-semibold text-slate-800">
                Version {rev.version}
                {rev.replan_reason ? (
                  <span className="ml-2 font-normal text-slate-500">
                    — {String(rev.replan_reason).replace(/_/g, ' ')}
                  </span>
                ) : null}
              </div>
              {(rev.changes || []).length ? (
                <p className="mt-1 text-xs text-slate-500">
                  {(rev.changes || []).slice(0, 4).join(' · ').replace(/_/g, ' ')}
                </p>
              ) : null}
              <ul className="mt-2 space-y-0.5">
                {(rev.steps || []).slice(0, 6).map((s) => (
                  <li key={`${rev.version}-${s.index}`} className="text-xs text-slate-600">
                    {s.status === 'success' ? '✓' : '○'} {s.index}. {s.description}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
