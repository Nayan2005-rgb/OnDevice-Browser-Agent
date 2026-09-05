import React, { useEffect, useState } from 'react';

export default function PrivacyStatus() {
  const [privacy, setPrivacy] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch('/api/privacy/status')
      .then((res) => res.json())
      .then((data) => setPrivacy(data))
      .catch((err) => setError(err.message));
  }, []);

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <h2 className="font-semibold mb-2">Privacy Status</h2>
      {error && <p className="text-sm text-red-500">Error: {error}</p>}
      {privacy && (
        <p className="text-sm text-slate-500">
          PII Redaction: {privacy.pii_redaction ? 'On' : 'Off'} · Face Blur:{' '}
          {privacy.face_blur ? 'On' : 'Off'}
        </p>
      )}
    </div>
  );
}