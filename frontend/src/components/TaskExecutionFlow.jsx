import React from 'react';

const STAGES = [
  'perceive',
  'plan',
  'resolve',
  'execute',
  'verify',
  'replan',
  'next_step',
];

const LABELS = {
  perceive: 'PERCEIVE',
  plan: 'PLAN',
  resolve: 'RESOLVE',
  execute: 'EXECUTE',
  verify: 'VERIFY',
  replan: 'REPLAN',
  next_step: 'NEXT STEP',
};

export default function TaskExecutionFlow({ plan }) {
  const active = (plan && plan.active_stage) || 'plan';

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <h2 className="mb-3 font-semibold">Execution Pipeline</h2>
      <div className="flex flex-col items-stretch gap-1">
        {STAGES.map((stage, i) => {
          const isActive = stage === active;
          return (
            <React.Fragment key={stage}>
              <div
                className={`rounded-md px-3 py-2 text-center text-xs font-semibold tracking-wide ${
                  isActive
                    ? 'bg-teal-700 text-white'
                    : 'bg-slate-50 text-slate-500'
                }`}
              >
                {LABELS[stage]}
              </div>
              {i < STAGES.length - 1 ? (
                <div className="text-center text-slate-300">↓</div>
              ) : null}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}
