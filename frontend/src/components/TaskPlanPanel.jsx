import React, { useEffect, useState } from 'react';
import TaskPlanTimeline from './TaskPlanTimeline.jsx';
import TaskPlanProgress from './TaskPlanProgress.jsx';
import TaskExecutionFlow from './TaskExecutionFlow.jsx';
import PageChangeIndicator from './PageChangeIndicator.jsx';
import ReplanHistory from './ReplanHistory.jsx';
import UserInterventionPanel from './UserInterventionPanel.jsx';

export default function TaskPlanPanel() {
  const [plan, setPlan] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const statusRes = await fetch('/api/agent/status');
        const statusData = await statusRes.json();
        const planId = statusData.plan_id;
        if (!planId) {
          if (!cancelled) setPlan(null);
          return;
        }
        const planRes = await fetch(`/api/agent/plan/${encodeURIComponent(planId)}`);
        if (!planRes.ok) {
          if (!cancelled) setPlan(null);
          return;
        }
        const planData = await planRes.json();
        if (!cancelled) {
          setPlan(planData.plan || null);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    };

    load();
    const id = setInterval(load, 1500);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (error) {
    return (
      <div className="rounded-xl border p-4 shadow-sm md:col-span-2">
        <h2 className="mb-2 font-semibold">Task Plan</h2>
        <p className="text-sm text-red-500">Error: {error}</p>
      </div>
    );
  }

  return (
    <>
      <div className="md:col-span-2">
        <TaskPlanTimeline plan={plan} />
      </div>
      <TaskPlanProgress plan={plan} />
      <TaskExecutionFlow plan={plan} />
      <PageChangeIndicator plan={plan} />
      <UserInterventionPanel plan={plan} />
      <div className="md:col-span-2">
        <ReplanHistory plan={plan} />
      </div>
    </>
  );
}
