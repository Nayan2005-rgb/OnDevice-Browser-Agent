import React, { useState } from 'react';

export default function Settings() {
  const [piiRedaction, setPiiRedaction] = useState(true);
  const [faceBlur, setFaceBlur] = useState(true);

  return (
    <div className="p-8 max-w-lg">
      <h1 className="text-xl font-bold mb-4">Settings</h1>

      <label className="flex items-center justify-between py-2 border-b">
        <span>PII Redaction</span>
        <input
          type="checkbox"
          checked={piiRedaction}
          onChange={(e) => setPiiRedaction(e.target.checked)}
        />
      </label>

      <label className="flex items-center justify-between py-2 border-b">
        <span>Face Blurring</span>
        <input
          type="checkbox"
          checked={faceBlur}
          onChange={(e) => setFaceBlur(e.target.checked)}
        />
      </label>
    </div>
  );
}
