import React, { useEffect, useState } from 'react';

const API = '/api/agent';

export default function ActionConfirmation() {
  const [confirmation, setConfirmation] = useState(null);
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const [expired, setExpired] = useState(false);

  const load = () => {
    fetch(`${API}/pending-confirmation`)
      .then((res) => res.json())
      .then((data) => {
        if (data.status === 'requires_confirmation' && data.confirmation) {
          setConfirmation(data.confirmation);
          setExpired(
            (data.confirmation.expires_in_seconds || 0) <= 0 ||
              data.confirmation.state === 'expired'
          );
        } else {
          setConfirmation(null);
          setExpired(false);
        }
      })
      .catch(() => {});
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 2000);
    return () => clearInterval(id);
  }, []);

  if (!confirmation) {
    return null;
  }

  const conf = Math.round(Number(confirmation.target?.confidence || 0) * 100);
  const source = confirmation.target?.source || 'dom';
  const sourceLabel =
    source === 'dom+vision'
      ? 'DOM + Vision'
      : source === 'vision'
        ? 'Vision'
        : 'DOM';

  const onCancel = async () => {
    setLoading(true);
    setFeedback(null);
    try {
      const res = await fetch(`${API}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmation_id: confirmation.id }),
      });
      const data = await res.json();
      setFeedback(
        data.status === 'cancelled'
          ? 'Action cancelled.'
          : `Cancel result: ${data.status}`
      );
      if (data.status === 'cancelled' || data.status === 'expired') {
        setConfirmation(null);
      }
    } catch (err) {
      setFeedback(err.message || 'Cancel failed');
    } finally {
      setLoading(false);
    }
  };

  const onConfirm = async () => {
    setLoading(true);
    setFeedback(null);
    try {
      const res = await fetch(`${API}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmation_id: confirmation.id }),
      });
      const data = await res.json();
      if (data.status === 'approved') {
        setFeedback(
          data.execution_status === 'waiting_for_extension'
            ? 'Confirmation approved. Waiting for browser…'
            : 'Action approved. Waiting for browser execution.'
        );
        setConfirmation(null);
      } else if (data.status === 'expired') {
        setExpired(true);
        setFeedback('Confirmation expired.');
      } else {
        setFeedback(`Confirmation result: ${data.status}`);
      }
    } catch (err) {
      setFeedback(err.message || 'Confirm failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        className="w-full max-w-md rounded-xl border border-amber-200 bg-white p-5 shadow-lg"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
      >
        <h2
          id="confirm-title"
          className="text-base font-semibold text-amber-800"
        >
          ⚠ Action Requires Confirmation
        </h2>

        <dl className="mt-4 space-y-2 text-sm">
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-400">
              Action
            </dt>
            <dd className="font-medium text-slate-800">
              {String(confirmation.action || 'click')}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-400">
              Target
            </dt>
            <dd className="font-medium text-slate-800">
              {confirmation.target?.label || 'Unknown'}{' '}
              <span className="text-slate-500">
                ({confirmation.target?.type || 'button'})
              </span>
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-400">
              Category
            </dt>
            <dd className="font-medium capitalize text-slate-800">
              {String(confirmation.category || 'unknown').replace(/_/g, ' ')}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-400">
              Detection
            </dt>
            <dd className="text-slate-700">{sourceLabel}</dd>
          </div>
          {conf > 0 && (
            <div>
              <dt className="text-xs uppercase tracking-wide text-slate-400">
                Confidence
              </dt>
              <dd className="text-slate-700">{conf}%</dd>
            </div>
          )}
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-400">
              Reason
            </dt>
            <dd className="text-slate-700">{confirmation.reason}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-400">
              Expires in
            </dt>
            <dd className="text-slate-700">
              {expired
                ? 'Expired'
                : `${confirmation.expires_in_seconds ?? 0}s`}
            </dd>
          </div>
        </dl>

        <hr className="my-4 border-slate-100" />

        {feedback && (
          <p className="mb-3 text-sm text-slate-600">{feedback}</p>
        )}

        <div className="flex justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={loading}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={loading || expired}
            className="rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
          >
            {loading ? 'Working…' : 'Confirm Action'}
          </button>
        </div>
      </div>
    </div>
  );
}
