import React from 'react';
import PrivacyStatus from './PrivacyStatus.jsx';
import ScreenPreview from './ScreenPreview.jsx';
import AgentStatus from './AgentStatus.jsx';
import ActionHistory from './ActionHistory.jsx';
import VisualPrivacyReport from './VisualPrivacyReport.jsx';
import ActionConfirmation from './ActionConfirmation.jsx';
import ActionLifecycle from './ActionLifecycle.jsx';
import TaskPlanPanel from './TaskPlanPanel.jsx';
import AgentSessionPanel from './AgentSessionPanel.jsx';
import ActionRecoveryCenter from './ActionRecoveryCenter.jsx';

export default function Dashboard() {
  return (
    <div className="grid grid-cols-1 gap-4 p-6 md:grid-cols-2">
      <AgentSessionPanel />
      <ActionConfirmation />
      <AgentStatus />
      <PrivacyStatus />
      <div className="md:col-span-2">
        <ScreenPreview />
      </div>
      <TaskPlanPanel />
      <VisualPrivacyReport />
      <ActionLifecycle />
      <ActionRecoveryCenter />
      <div className="md:col-span-2">
        <ActionHistory />
      </div>
    </div>
  );
}
