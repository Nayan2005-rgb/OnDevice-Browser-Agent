import React from 'react';
import { Link } from 'react-router-dom';

export default function Home() {
  return (
    <div className="p-8">
      <h1 className="text-2xl font-bold mb-2">OnDevice Browser Agent</h1>
      <p className="text-slate-600 mb-4">
        A privacy-first, on-device browser automation agent. All perception and
        redaction happen locally before any data is sent to the LLM backend.
      </p>
      <Link to="/dashboard" className="text-blue-600 underline">
        Go to Dashboard
      </Link>
    </div>
  );
}
