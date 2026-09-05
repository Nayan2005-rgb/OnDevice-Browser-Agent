import React from 'react';
import { Link } from 'react-router-dom';

export default function Navbar() {
  return (
    <nav className="flex items-center justify-between px-6 py-4 bg-slate-900 text-white">
      <Link to="/" className="font-semibold text-lg">OnDevice Browser Agent</Link>
      <div className="flex gap-4 text-sm">
        <Link to="/dashboard">Dashboard</Link>
        <Link to="/settings">Settings</Link>
      </div>
    </nav>
  );
}
