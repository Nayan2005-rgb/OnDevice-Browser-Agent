import React, { useCallback, useEffect, useState } from 'react';
import SessionStatusBadge from './SessionStatusBadge.jsx';
import SessionControls from './SessionControls.jsx';
import SessionTimeline from './SessionTimeline.jsx';
import SessionRecoveryStatus from './SessionRecoveryStatus.jsx';
import AgentSessionCard from './AgentSessionCard.jsx';
import ReplanPreview from './ReplanPreview.jsx';

export default function AgentSessionPanel() {
  const [sessions, setSessions] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [bundle, setBundle] = useState(null);
  const [error, setError] = useState(null);

  const loadList = useCallback(async () => {
    try {
      const res = await fetch('/api/agent/sessions?limit=20');
      if (!res.ok) {
        if (res.status === 503) {
          setError('Persistent storage unavailable');
          return;
        }
        throw new Error(`HTTP ${res.status}`);
      }
      const data = await res.json();
      setSessions(data.sessions || []);
      setError(null);
      if (!selectedId && data.sessions && data.sessions[0]) {
        setSelectedId(data.sessions[0].session_id);
      }
    } catch (err) {
      // Fallback: try status for session_id
      try {
        const st = await fetch('/api/agent/status');
        const sd = await st.json();
        if (sd.session_id) setSelectedId(sd.session_id);
      } catch (_) {
        setError(err.message);
      }
    }
  }, [selectedId]);

  const loadBundle = useCallback(async (id) => {
    if (!id) {
      setBundle(null);
      return;
    }
    try {
      const res = await fetch(`/api/agent/sessions/${encodeURIComponent(id)}`);
      if (!res.ok) {
        setBundle(null);
        return;
      }
      const data = await res.json();
      setBundle(data);
      setError(null);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    loadList();
    const id = setInterval(loadList, 2500);
    return () => clearInterval(id);
  }, [loadList]);

  useEffect(() => {
    loadBundle(selectedId);
    if (!selectedId) return undefined;
    const id = setInterval(() => loadBundle(selectedId), 2000);
    return () => clearInterval(id);
  }, [selectedId, loadBundle]);

  const session = (bundle && bundle.session) || null;
  const plan = (bundle && bundle.plan) || null;
  const preview = (bundle && bundle.replan_preview) || null;
  const timeline = (bundle && bundle.timeline) || [];

  const total = plan?.total_steps || 0;
  const current = plan?.current_step || session?.current_step_index || 0;
  const pct = total ? Math.min(100, Math.round((Number(current) / total) * 100)) : 0;

  return (
    <>
      <div className="rounded-xl border bg-gradient-to-br from-slate-50 to-teal-50/40 p-4 shadow-sm md:col-span-2">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
              On-Device Browser Agent
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {session ? (
                <>
                  <SessionStatusBadge status={session.status} />
                  <span className="font-mono text-lg font-semibold text-slate-900">
                    Session {session.session_label || session.session_id}
                  </span>
                </>
              ) : (
                <span className="text-sm text-slate-500">No persistent session selected</span>
              )}
            </div>
            <p className="mt-1 text-xs text-emerald-800">Privacy Safe ✓ · Local SQLite only</p>
          </div>
          {session ? (
            <div className="text-right text-sm text-slate-600">
              <div>Plan v{session.plan_version || 1}</div>
              <div className="text-xs text-slate-400">
                Replans {session.replan_count || 0}
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <div className="rounded-xl border p-4 shadow-sm">
        <h2 className="mb-2 font-semibold">Current Task</h2>
        {session || plan ? (
          <>
            <p className="text-sm text-slate-800">{session?.goal || plan?.goal || '—'}</p>
            <p className="mt-2 text-xs text-slate-500">
              Step {current || 0} / {total || '—'}
            </p>
            <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-teal-600 transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
          </>
        ) : (
          <p className="text-sm text-slate-500">Start a multi-step plan to create a session.</p>
        )}
      </div>

      <SessionControls
        sessionId={selectedId}
        status={session?.status}
        onUpdated={() => {
          loadList();
          loadBundle(selectedId);
        }}
      />

      <SessionRecoveryStatus session={session} />

      <div className="rounded-xl border p-4 shadow-sm">
        <h2 className="mb-3 font-semibold">Recent Sessions</h2>
        <div className="max-h-56 space-y-2 overflow-y-auto">
          {sessions.length === 0 ? (
            <p className="text-sm text-slate-500">No sessions yet.</p>
          ) : (
            sessions.map((s) => (
              <AgentSessionCard
                key={s.session_id}
                session={s}
                selected={s.session_id === selectedId}
                onSelect={(sess) => setSelectedId(sess.session_id)}
              />
            ))
          )}
        </div>
      </div>

      {preview && preview.status === 'pending' ? (
        <ReplanPreview
          sessionId={selectedId}
          preview={preview}
          onResolved={() => loadBundle(selectedId)}
        />
      ) : null}

      <SessionTimeline events={timeline} />

      {error ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700 md:col-span-2">
          {error}
        </div>
      ) : null}
    </>
  );
}
