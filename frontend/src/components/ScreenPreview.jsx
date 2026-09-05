import React, { useEffect, useState } from 'react';

export default function ScreenPreview() {
  const [image, setImage] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchScreenshot = () => {
      fetch('/api/agent/screenshot')
        .then((res) => res.json())
        .then((data) => setImage(data.image))
        .catch((err) => setError(err.message));
    };

    fetchScreenshot();
    const interval = setInterval(fetchScreenshot, 3000); // poll every 3s
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="rounded-xl border p-4 shadow-sm">
      <h2 className="font-semibold mb-2">Screen Preview</h2>
      <div className="aspect-video bg-slate-100 flex items-center justify-center text-slate-400 overflow-hidden">
        {error && <p className="text-red-500 text-sm">Error: {error}</p>}
        {!error && image && (
          <img src={image} alt="Screen preview" className="w-full h-full object-contain" />
        )}
        {!error && !image && <p>No active session</p>}
      </div>
    </div>
  );
}