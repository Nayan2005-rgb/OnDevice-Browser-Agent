// Background service worker: coordinates messages between the popup,
// content scripts, and the local agent backend.

const API_BASE = 'http://127.0.0.1:5000/api';

chrome.runtime.onInstalled.addListener(() => {
  console.log('OnDevice Browser Agent installed.');
});

async function callAgentStep(goal) {
  const response = await fetch(`${API_BASE}/agent/step`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      goal: goal || 'No goal set',
      dom_snapshot: {},
      page_text: '',
    }),
  });
  return response.json();
}

async function fetchAgentStatus() {
  const response = await fetch(`${API_BASE}/agent/status`);
  return response.json();
}

function captureVisibleTab() {
  return new Promise((resolve, reject) => {
    chrome.tabs.captureVisibleTab(null, { format: 'png' }, (dataUrl) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve(dataUrl);
      }
    });
  });
}

async function sendScreenshotToBackend() {
  const dataUrl = await captureVisibleTab();
  await fetch(`${API_BASE}/agent/screenshot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image: dataUrl }),
  });
  return dataUrl;
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {
    case 'GET_STATUS':
      fetchAgentStatus()
        .then((data) => sendResponse({ ok: true, data }))
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;

    case 'ACTION_EXECUTE':
      callAgentStep(message.goal)
        .then((data) => sendResponse({ ok: true, data }))
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;

    case 'CAPTURE_REQUEST':
      sendScreenshotToBackend()
        .then((dataUrl) => sendResponse({ ok: true, dataUrl }))
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;

    default:
      sendResponse({ ok: false, error: 'unknown_message_type' });
  }
});