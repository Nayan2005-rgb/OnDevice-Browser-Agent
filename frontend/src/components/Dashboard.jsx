import React from 'react';
import PrivacyStatus from './PrivacyStatus.jsx';
import ScreenPreview from './ScreenPreview.jsx';
import AgentStatus from './AgentStatus.jsx';
import ActionHistory from './ActionHistory.jsx';

export default function Dashboard() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 p-6">
      <AgentStatus />
      <PrivacyStatus />
      <ScreenPreview />
      <ActionHistory />
    </div>
  );
}
