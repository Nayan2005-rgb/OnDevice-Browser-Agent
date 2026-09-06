import React, { useEffect, useState } from 'react';

const STATE_LABELS = {
  created: 'Created',
  resolved: 'Target Resolved',
  requires_confirmation: 'Confirmation Required',
  approved: 'Confirmation Approved',
  waiting_for_extension: 'Waiting for Browser',
  claimed: 'Claimed by Browser',
  executing: 'Executing Action',
  executed: 'Executed',
  verifying: 'Verifying Result',
  success: 'Success',
  failed: 'Failed',
  unclear: 'Unclear',
  recovering: 'Recovery Attempt',
  recovery_required: 'Recovery Required',
  cancelled: 'Cancelled',
  expired: 'Expired',
  blocked: 'Blocked',
};

function markFor(state, current, timelineStates) {
  if (timelineStates.includes(state) || state === current) {
    if (
      state === 'failed' ||
      state === 'requires_confirmation' ||
      state === 'unclear' ||
      state === 'expired' ||
      state === 'waiting_for_extension'
    ) {
      return state === 'waiting_for_extension' ? '⏳' : '⚠';
    }
    if (state === 'executing' || state === 'verifying' || state === 'claimed') {
      return state === 'verifying' ? '🔍' : '⚙';
    }
    if (state === 'cancelled' || state === 'blocked') return '✗';
    return '✓';
  }
  return '·';
}

function friendlyStatus(state) {
  const map = {
    waiting_for_extension: 'Waiting for browser',
    claimed: 'Executing action',
    executing: 'Executing action',
    verifying: 'Verifying result',
    recovering: 'Recovery attempt',
    success: 'Success',
    failed: 'Failed',
    expired: 'Expired',
    cancelled: 'Cancelled',
  };
  return map[state] || STATE_LABELS[state] || state;
}

export default function ActionLifecycle() {
  const [lifecycle, setLifecycle] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const load = () => {
      fetch('/api/agent/lifecycle')
        .then((res) => res.json())
        .then((data) => setLifecycle(data.lifecycle || null))
        .catch((err) => setError(err.message));
    };
    load();
    const id = setInterval(load, 1500);
    return () => clearInterval(id);
  }, []);

  if (error) {
    return (
      <div className="rounded-xl border p-4 shadow-sm">
        <h2 className="mb-2 font-semibold">Action Lifecycle</h2>
        <p className="text-sm text-red-500">Error: {error}</p>
      </div>
    );
  }

  if (!lifecycle) {
    return (
      <div className="rounded-xl border p-4 shadow-sm">
        <h2 className="mb-2 font-semibold">Action Lifecycle</h2>
        <p className="text-sm text-slate-500">No active action lifecycle.</p>
      </div>
    );
  }

  const timelineStates = (lifecycle.timeline || []).map((t) => t.state);
  const steps = lifecycle.timeline?.length
    ? lifecycle.timeline
    : [{ state: lifecycle.state }];

  const conf =
    lifecycle.confidence != null
      ? Math.round(Number(lifecycle.confidence) * 100)
      : null;
  const totalMs = lifecycle.performance?.total_action_lifecycle_ms;
  const verification = lifecycle.verification;
  const recovery = lifecycle.recovery_attempts || [];
  const recoveryLabel =
    recovery.length > 0 ? `Recovery attempt ${recovery.length}` : null;

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <h2 className="mb-2 font-semibold">Action Lifecycle</h2>
      {lifecycle.task && (
        <p className="mb-3 truncate text-xs text-slate-500">
          Task: {lifecycle.task}
        </p>
      )}

      <div className="mb-3 font-mono text-sm leading-relaxed text-slate-700">
        <div>TASK</div>
        {steps.map((step, i) => {
          const label = STATE_LABELS[step.state] || step.state;
          const mark = markFor(step.state, lifecycle.state, timelineStates);
          return (
            <div key={`${step.state}-${i}`}>
              {i === steps.length - 1 ? ' └── ' : ' ├── '}
              {label} {mark}
            </div>
          );
        })}
      </div>

      <div className="space-y-1 text-xs text-slate-500">
        <div>
          Current status:{' '}
          <span className="font-medium text-slate-700">
            {recoveryLabel || friendlyStatus(lifecycle.state)}
          </span>
        </div>
        {lifecycle.execution_status &&
          lifecycle.execution_status !== lifecycle.state && (
            <div>Execution: {friendlyStatus(lifecycle.execution_status)}</div>
          )}
        {lifecycle.strategy && <div>Strategy: {lifecycle.strategy}</div>}
        {lifecycle.source && <div>Source: {lifecycle.source}</div>}
        {conf != null && <div>Confidence: {conf}%</div>}
        {verification && (
          <div>
            Verification: {verification.status}
            {verification.reason ? ` — ${verification.reason}` : ''}
          </div>
        )}
        {recovery.length > 0 && (
          <div>
            Recovery attempts: {recovery.length}
            <ul className="ml-3 list-disc">
              {recovery.map((r, i) => (
                <li key={i}>
                  #{r.attempt} {r.strategy}
                  {r.reason ? ` — ${r.reason}` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}
        {totalMs != null && (
          <div>Total lifecycle: {Math.round(Number(totalMs))} ms</div>
        )}
        {lifecycle.performance?.confirmation_approval_ms != null && (
          <div>
            Confirm→queue:{' '}
            {Math.round(Number(lifecycle.performance.confirmation_approval_ms))}{' '}
            ms
          </div>
        )}
        {lifecycle.performance?.approved_action_wait_ms != null && (
          <div>
            Wait for claim:{' '}
            {Math.round(Number(lifecycle.performance.approved_action_wait_ms))}{' '}
            ms
          </div>
        )}
      </div>
    </div>
  );
}
