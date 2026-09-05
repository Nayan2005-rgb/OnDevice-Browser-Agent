document.addEventListener('DOMContentLoaded', () => {
  const statusEl = document.getElementById('status');
  const toggleBtn = document.getElementById('toggle-agent');
  const resultEl = document.getElementById('result');

  let running = false;

  function refreshStatus() {
    chrome.runtime.sendMessage({ type: 'GET_STATUS' }, (response) => {
      if (response && response.ok) {
        statusEl.textContent = `Backend status: ${response.data.status}`;
      } else {
        statusEl.textContent = `Error: ${response ? response.error : 'no response'}`;
      }
    });
  }

    const captureBtn = document.getElementById('capture-btn');
  captureBtn.addEventListener('click', () => {
    resultEl.textContent = 'Capturing...';
    chrome.runtime.sendMessage({ type: 'CAPTURE_REQUEST' }, (response) => {
      if (response && response.ok) {
        resultEl.textContent = 'Screenshot sent to dashboard.';
      } else {
        resultEl.textContent = `Error: ${response ? response.error : 'no response'}`;
      }
    });
  });

  toggleBtn.addEventListener('click', () => {
    running = !running;
    toggleBtn.textContent = running ? 'Stop Agent' : 'Start Agent';

    if (running) {
      resultEl.textContent = 'Running agent step...';
      chrome.runtime.sendMessage(
        { type: 'ACTION_EXECUTE', goal: 'Test connection to backend' },
        (response) => {
          if (response && response.ok) {
            resultEl.textContent = JSON.stringify(response.data, null, 2);
          } else {
            resultEl.textContent = `Error: ${response ? response.error : 'no response'}`;
          }
        }
      );
    }
  });

  refreshStatus();
});