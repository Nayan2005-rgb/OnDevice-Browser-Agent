document.addEventListener('DOMContentLoaded', () => {
  const statusEl = document.getElementById('status');
  const toggleBtn = document.getElementById('toggle-agent');
  const runPlanBtn = document.getElementById('run-plan-btn');
  const cancelPlanBtn = document.getElementById('cancel-plan-btn');
  const resultEl = document.getElementById('result');
  const taskInput = document.getElementById('task-input');
  const captureBtn = document.getElementById('capture-btn');
  const execStateEl = document.getElementById('execution-state');
  let lastPlanId = null;

  function formatExecutionUi(ui, backendStatus) {
    const state = (ui && ui.state) || 'idle';
    const labels = {
      idle: '',
      waiting_for_confirmation: '⚠ Waiting for confirmation',
      confirmation_received: '⏳ Confirmation received',
      executing: '⚙ Executing action',
      executed: '✓ Action executed',
      verified: '✓ Action verified',
      failed: '⚠ Execution failed',
      expired: '⚠ Confirmation expired',
    };
    if (backendStatus === 'requires_confirmation' && state === 'idle') {
      return '⚠ Waiting for confirmation';
    }
    if (backendStatus === 'waiting_for_extension') {
      return labels.confirmation_received;
    }
    return labels[state] || '';
  }

  function refreshStatus() {
    chrome.runtime.sendMessage({ type: 'GET_STATUS' }, (response) => {
      if (response && response.ok) {
        const data = response.data || {};
        statusEl.textContent = `Backend status: ${data.status}`;
        if (data.plan_id) lastPlanId = data.plan_id;
        if (execStateEl) {
          const line = formatExecutionUi(data.execution_ui, data.status);
          execStateEl.textContent = line;
          execStateEl.hidden = !line;
        }
      } else {
        statusEl.textContent = `Error: ${response ? response.error : 'no response'}`;
      }
    });
  }

  captureBtn.addEventListener('click', () => {
    resultEl.textContent = 'Capturing + redacting locally...';
    chrome.runtime.sendMessage({ type: 'CAPTURE_REQUEST' }, (response) => {
      if (response && response.ok) {
        const report = response.screenshot_privacy_report || {};
        const timing = response.timing || {};
        const vision = report.vision || {};
        const vc = response.visual_context || {};
        const faces =
          vision.faces_detected != null
            ? vision.faces_detected
            : vc.faces_detected != null
              ? vc.faces_detected
              : 0;
        const lines = [
          response.sanitized
            ? 'Sanitized screenshot sent to dashboard (raw stayed local).'
            : 'Screenshot sent (privacy mode off).',
          `Boxes: ${response.box_count != null ? response.box_count : '?'}`,
          `Faces: ${faces}`,
          `Redactions: ${report.total_redactions != null ? report.total_redactions : 0}`,
          `Face detect: ${
            timing.face_detection_ms != null ? timing.face_detection_ms : '?'
          } ms`,
          `Processing: ${
            report.processing_time_ms != null
              ? report.processing_time_ms
              : timing.total_privacy_processing_ms || '?'
          } ms`,
        ];
        resultEl.textContent = lines.join('\n');
      } else {
        resultEl.textContent = `Error: ${response ? response.error : 'no response'}`;
      }
    });
  });

  toggleBtn.addEventListener('click', () => {
    const task = (taskInput && taskInput.value.trim()) || 'Click the Submit button';
    resultEl.textContent = 'Running agent step...';
    toggleBtn.disabled = true;

    chrome.runtime.sendMessage(
      { type: 'RUN_AGENT_STEP', task, goal: task },
      (response) => {
        toggleBtn.disabled = false;
        if (response && response.ok) {
          const data = response.data || {};
          if (data.status === 'requires_confirmation') {
            resultEl.textContent =
              '⚠ Waiting for confirmation in the dashboard.\n' +
              'After you confirm, this tab will execute the stored action once.';
          } else {
            resultEl.textContent = JSON.stringify(data, null, 2);
          }
        } else {
          resultEl.textContent = `Error: ${response ? response.error : 'no response'}`;
        }
        refreshStatus();
      }
    );
  });

  if (runPlanBtn) {
    runPlanBtn.addEventListener('click', () => {
      const goal =
        (taskInput && taskInput.value.trim()) ||
        'Search for AI courses and open the first result';
      resultEl.textContent = 'Running multi-step task plan...';
      runPlanBtn.disabled = true;
      toggleBtn.disabled = true;

      chrome.runtime.sendMessage(
        { type: 'RUN_TASK_PLAN', goal, task: goal },
        (response) => {
          runPlanBtn.disabled = false;
          toggleBtn.disabled = false;
          if (response && response.ok) {
            const data = response.data || {};
            lastPlanId = data.plan_id || (data.plan && data.plan.plan_id) || lastPlanId;
            if (
              data.status === 'requires_confirmation' ||
              data.status === 'waiting_for_confirmation'
            ) {
              resultEl.textContent =
                '⚠ Plan paused for confirmation.\n' +
                'Approve in the dashboard; this tab will claim and execute once.\n' +
                `plan_id: ${lastPlanId || '?'}`;
            } else {
              const plan = data.plan || {};
              resultEl.textContent =
                `Plan ${plan.status || data.status}\n` +
                `Steps: ${(plan.current_step || '?')} / ${(plan.total_steps || '?')}\n` +
                JSON.stringify(
                  {
                    plan_id: lastPlanId,
                    status: data.status,
                    plan_status: plan.status,
                    metrics: plan.metrics,
                  },
                  null,
                  2
                );
            }
          } else {
            resultEl.textContent = `Error: ${response ? response.error : 'no response'}`;
          }
          refreshStatus();
        }
      );
    });
  }

  if (cancelPlanBtn) {
    cancelPlanBtn.addEventListener('click', () => {
      resultEl.textContent = 'Cancelling plan...';
      chrome.runtime.sendMessage(
        { type: 'CANCEL_TASK_PLAN', plan_id: lastPlanId },
        (response) => {
          if (response && response.ok) {
            resultEl.textContent = JSON.stringify(response.data, null, 2);
          } else {
            resultEl.textContent = `Error: ${response ? response.error : 'no response'}`;
          }
          refreshStatus();
        }
      );
    });
  }

  refreshStatus();
  setInterval(refreshStatus, 1500);
});
