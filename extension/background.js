// Background service worker: coordinates messages between the popup,
// content scripts, and the local agent backend.
//
// PRIVACY BOUNDARY: This worker only ever forwards already-sanitized
// snapshots from the content script. It must never scrape the page itself.
//
// SCREENSHOT PRIVACY BOUNDARY (Milestone 2A / 2B):
//   RAW captureVisibleTab
//     → local face detection (ephemeral on-device worker, boxes only)
//     → unify DOM PII boxes + face boxes
//     → local Canvas redaction
//     → SANITIZED only → network / dashboard
// Never POST a raw screenshot when privacy mode is enabled.

importScripts('screenshotRedactor.js');
importScripts('faceDetector.js');

const API_BASE = 'http://127.0.0.1:5000/api';

// Privacy mode defaults ON. Raw screenshots must not cross the network.
const PRIVACY_MODE_DEFAULT = true;

const FACE_CONFIG = {
  faceDetection: true,
  minConfidence: 0.6,
  paddingPercent: 10,
  blurStrength: 'adaptive',
};

const APPROVED_ACTION_POLL_MS = 750;
const APPROVED_ACTION_POLL_MAX_MS = 70000;

// Latest safe visual metadata from capture (no raw image)
let _latestVisualContext = null;
let _latestVisualUiMap = null;

// Active confirmation → approved-action poll sessions (keyed by tabId)
const _pollSessions = new Map();

chrome.runtime.onInstalled.addListener(() => {
  console.log('OnDevice Browser Agent installed.');
  chrome.storage.local.set({ privacyMode: PRIVACY_MODE_DEFAULT });
});

// Service worker restart: resume polling only if we still have a pending
// confirmation context. Never claim actions for unknown tabs.
chrome.runtime.onStartup.addListener(() => {
  resumePollingFromStorage();
});

function resumePollingFromStorage() {
  try {
    chrome.storage.local.get(
      {
        executionUiState: 'idle',
        confirmationId: null,
        tabId: null,
      },
      (local) => {
        const pendingStates = {
          waiting_for_confirmation: true,
          confirmation_received: true,
        };
        if (
          !pendingStates[local.executionUiState] ||
          !local.confirmationId ||
          local.tabId == null
        ) {
          return;
        }
        startApprovedActionPoll(local.tabId, {
          confirmationId: local.confirmationId,
        });
      }
    );
  } catch (_) {
    // Fail closed — do not execute without explicit poll context
  }
}

// Also attempt resume when the worker wakes via a message
resumePollingFromStorage();
reconnectActiveSession();

/**
 * Milestone 5A: on extension start, check for an active backend session for
 * the current tab and send fresh sanitized perception for recovery.
 * Stores only session_id / plan_id / tab_id locally — never raw page data.
 */
function reconnectActiveSession() {
  try {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const tab = tabs && tabs[0];
      if (!tab || tab.id == null) return;
      fetch(
        `${API_BASE}/agent/sessions/active?tab_id=${encodeURIComponent(tab.id)}`
      )
        .then((r) => (r.ok ? r.json() : null))
        .then(async (data) => {
          if (!data || !data.session) return;
          const session = data.session;
          try {
            chrome.storage.local.set({
              sessionId: session.session_id,
              planId: session.plan_id || null,
              tabId: tab.id,
            });
          } catch (_) {
            /* ignore */
          }
          // Fresh sanitized snapshot only — never raw page storage
          const snap = await requestSanitizedSnapshot(tab.id);
          await fetch(
            `${API_BASE}/agent/sessions/${encodeURIComponent(session.session_id)}/recover`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                page: snap.page,
                privacy_report: snap.privacy_report,
                visual_context: _latestVisualContext || undefined,
                visual_ui_map:
                  _latestVisualUiMap && _latestVisualUiMap.privacy_safe
                    ? _latestVisualUiMap
                    : undefined,
                tab_id: tab.id,
                window_id: tab.windowId,
              }),
            }
          );
        })
        .catch(() => {
          // Fail closed — do not execute without backend validation
        });
    });
  } catch (_) {
    // ignore
  }
}

function storeSessionContext(sessionId, planId, tabId) {
  try {
    const payload = {};
    if (sessionId) payload.sessionId = sessionId;
    if (planId) payload.planId = planId;
    if (tabId != null) payload.tabId = tabId;
    chrome.storage.local.set(payload);
  } catch (_) {
    // ignore
  }
}

function setExecutionUiState(state, extra) {
  const payload = {
    executionUiState: state || 'idle',
    executionUiUpdatedAt: Date.now(),
    ...(extra || {}),
  };
  try {
    chrome.storage.local.set(payload);
  } catch (_) {
    // ignore
  }
}

function stopApprovedActionPoll(tabId) {
  const key = String(tabId);
  const session = _pollSessions.get(key);
  if (session && session.timer) {
    clearInterval(session.timer);
  }
  _pollSessions.delete(key);
}

function startApprovedActionPoll(tabId, meta) {
  const key = String(tabId);
  stopApprovedActionPoll(tabId);
  const session = {
    tabId,
    confirmationId: meta && meta.confirmationId,
    lifecycleId: meta && meta.lifecycleId,
    task: (meta && meta.task) || null,
    startedAt: Date.now(),
    claiming: false,
    timer: null,
  };
  session.timer = setInterval(() => {
    pollApprovedActionOnce(session).catch(() => {});
  }, APPROVED_ACTION_POLL_MS);
  _pollSessions.set(key, session);
  setExecutionUiState('waiting_for_confirmation', {
    confirmationId: session.confirmationId,
    tabId,
  });
  // Immediate first poll in case dashboard already confirmed
  pollApprovedActionOnce(session).catch(() => {});
}

async function pollApprovedActionOnce(session) {
  if (!session || session.claiming) return;
  if (Date.now() - session.startedAt > APPROVED_ACTION_POLL_MAX_MS) {
    stopApprovedActionPoll(session.tabId);
    setExecutionUiState('expired', {
      confirmationId: session.confirmationId,
      tabId: session.tabId,
    });
    return;
  }

  session.claiming = true;
  try {
    const response = await fetch(
      `${API_BASE}/agent/approved-action?tab_id=${encodeURIComponent(session.tabId)}`
    );
    if (!response.ok) return;
    const data = await response.json();
    if (!data || data.status !== 'approved' || !data.action) {
      return;
    }

    // Claimed — stop polling before execute (exactly once)
    stopApprovedActionPoll(session.tabId);
    setExecutionUiState('confirmation_received', {
      confirmationId: data.confirmation_id || session.confirmationId,
      executionId: data.execution_id,
      tabId: session.tabId,
    });
    await executeClaimedApprovedAction(session.tabId, data, session);
  } finally {
    session.claiming = false;
  }
}

async function executeClaimedApprovedAction(tabId, claimed, session) {
  const action = claimed.action;
  const confirmationId =
    claimed.confirmation_id || (session && session.confirmationId) || null;
  const executionId = claimed.execution_id;
  const lifecycleId =
    claimed.lifecycle_id || (session && session.lifecycleId) || null;
  const task = claimed.task || (session && session.task) || null;
  const targetSelector = action.selector || null;

  setExecutionUiState('executing', {
    confirmationId,
    executionId,
    tabId,
  });

  let execution = null;
  let preState = null;
  let postState = null;
  const tExec0 = performance.now();
  try {
    preState = await requestSafePageState(tabId, targetSelector);
    execution = await executeActionOnTab(tabId, action);
    const executionMs = Math.round(performance.now() - tExec0);
    if (execution && typeof execution === 'object') {
      execution.execution_time_ms =
        execution.execution_time_ms != null
          ? execution.execution_time_ms
          : executionMs;
    }
    postState = await verifyActionResult(tabId, targetSelector, 300);
  } catch (err) {
    execution = {
      success: false,
      status: 'failed',
      reason: err && err.message ? err.message : 'execution_error',
      target_found: false,
    };
  }

  const ok =
    execution && (execution.success === true || execution.status === 'success');
  const historyItem = {
    timestamp: new Date().toISOString(),
    task,
    action: action.type,
    status: ok ? 'success' : 'execution_failed',
    execution: {
      status: ok ? 'success' : 'failed',
      strategy: execution && execution.strategy,
      target_found: execution ? execution.target_found : false,
      element_tag:
        execution &&
        (execution.element_tag ||
          (execution.target && execution.target.tag)),
      execution_time_ms: execution && execution.execution_time_ms,
      action: action.type,
      reason: execution && (execution.reason || execution.error),
      success: ok,
    },
    execution_id: executionId,
    confirmation_id: confirmationId,
    tab_id: tabId,
    lifecycle_id: lifecycleId,
    pre_action_state: preState,
    post_action_state: postState,
    confirmation: {
      required: true,
      approved: true,
      id: confirmationId,
    },
    result: {
      strategy: (execution && execution.strategy) || 'selector',
      target_found: execution ? !!execution.target_found : false,
      status: ok ? 'success' : 'failed',
    },
  };

  const report = await reportExecution(historyItem);
  if (ok && report && report.verification && report.verification.status === 'success') {
    setExecutionUiState('verified', {
      confirmationId,
      executionId,
      tabId,
    });
  } else if (ok) {
    setExecutionUiState(report && report.ok === false ? 'failed' : 'executed', {
      confirmationId,
      executionId,
      tabId,
    });
  } else {
    setExecutionUiState('failed', {
      confirmationId,
      executionId,
      tabId,
    });
  }
  return { claimed, execution, report, history_item: historyItem };
}

function getActiveTab() {
  return new Promise((resolve, reject) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      if (!tabs || !tabs[0]) {
        reject(new Error('No active tab'));
        return;
      }
      resolve(tabs[0]);
    });
  });
}

function sendTabMessage(tabId, message) {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, message, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      resolve(response);
    });
  });
}

function getPrivacyMode() {
  return new Promise((resolve) => {
    chrome.storage.local.get({ privacyMode: PRIVACY_MODE_DEFAULT }, (result) => {
      resolve(result.privacyMode !== false);
    });
  });
}

/**
 * Ask the content script for a PRIVACY-SANITIZED DOM snapshot.
 * Raw page content never reaches this function.
 */
async function requestSanitizedSnapshot(tabId) {
  const response = await sendTabMessage(tabId, { type: 'GET_DOM_SNAPSHOT' });
  if (!response || response.ok === false) {
    throw new Error((response && response.error) || 'Failed to get DOM snapshot');
  }
  return {
    page: response.page,
    privacy_report: response.privacy_report || {},
  };
}

/**
 * Ask the content script for sensitive element bounding boxes (viewport CSS).
 * Response never includes raw PII values — only categories + geometry.
 */
async function requestSensitiveBoxes(tabId) {
  const response = await sendTabMessage(tabId, { type: 'GET_SENSITIVE_BOXES' });
  if (!response || response.ok === false) {
    throw new Error((response && response.error) || 'Failed to get sensitive boxes');
  }
  return {
    boxes: response.boxes || [],
    viewport: response.viewport || {},
    metrics: response.metrics || {},
  };
}

async function callAgentStep(task, page, privacyReport, visualContext, visualUiMap, tabMeta) {
  // Only sanitized page context + safe visual metadata are included.
  const body = {
    task: task || 'No task set',
    goal: task || 'No task set', // backward compatible
    page,
    privacy_report: privacyReport,
    visual_context: visualContext || undefined,
    // Legacy fields derived from sanitized page for older backend paths
    dom_snapshot: page,
    page_text: (page && page.visibleText) || '',
  };
  if (tabMeta && tabMeta.tabId != null) {
    body.tab_id = tabMeta.tabId;
  }
  if (tabMeta && tabMeta.windowId != null) {
    body.window_id = tabMeta.windowId;
  }
  // Only forward privacy-safe visual UI maps
  if (visualUiMap && visualUiMap.privacy_safe === true) {
    body.visual_ui_map = visualUiMap;
  }
  const response = await fetch(`${API_BASE}/agent/step`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Backend error: ${response.status}`);
  }
  return response.json();
}

async function executeActionOnTab(tabId, action) {
  return sendTabMessage(tabId, { type: 'EXECUTE_ACTION', action });
}

async function requestSafePageState(tabId, targetSelector) {
  const response = await sendTabMessage(tabId, {
    type: 'GET_SAFE_PAGE_STATE',
    targetSelector: targetSelector || null,
  });
  if (!response || response.ok === false) {
    return null;
  }
  return response.state || null;
}

async function verifyActionResult(tabId, targetSelector, delayMs) {
  const response = await sendTabMessage(tabId, {
    type: 'VERIFY_ACTION_RESULT',
    targetSelector: targetSelector || null,
    delay_ms: delayMs != null ? delayMs : 300,
  });
  if (!response || response.ok === false) {
    return null;
  }
  return response.state || null;
}

async function reportExecution(historyItem) {
  try {
    const response = await fetch(`${API_BASE}/agent/execution`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(historyItem),
    });
    if (!response.ok) {
      return { ok: false, error: `HTTP ${response.status}` };
    }
    return await response.json();
  } catch (err) {
    // Non-fatal for safe-path history; bridge path surfaces via UI state
    return { ok: false, error: err && err.message ? err.message : 'network' };
  }
}

async function callAgentPlan(goal, page, privacyReport, visualContext, visualUiMap, tabMeta, waitElapsedMs) {
  const body = {
    goal: goal || '',
    task: goal || '',
    page,
    privacy_report: privacyReport,
    visual_context: visualContext || undefined,
    dom_snapshot: page,
    page_text: (page && page.visibleText) || '',
  };
  if (tabMeta && tabMeta.tabId != null) body.tab_id = tabMeta.tabId;
  if (tabMeta && tabMeta.windowId != null) body.window_id = tabMeta.windowId;
  if (visualUiMap && visualUiMap.privacy_safe === true) {
    body.visual_ui_map = visualUiMap;
  }
  if (waitElapsedMs != null) body.wait_elapsed_ms = waitElapsedMs;
  const response = await fetch(`${API_BASE}/agent/plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Backend error: ${response.status}`);
  }
  return response.json();
}

async function callAgentPlanResume(planId, page, privacyReport, visualContext, visualUiMap, tabMeta, waitElapsedMs) {
  const body = {
    plan_id: planId,
    page,
    privacy_report: privacyReport,
    visual_context: visualContext || undefined,
    dom_snapshot: page,
    page_text: (page && page.visibleText) || '',
  };
  if (tabMeta && tabMeta.tabId != null) body.tab_id = tabMeta.tabId;
  if (tabMeta && tabMeta.windowId != null) body.window_id = tabMeta.windowId;
  if (visualUiMap && visualUiMap.privacy_safe === true) {
    body.visual_ui_map = visualUiMap;
  }
  if (waitElapsedMs != null) body.wait_elapsed_ms = waitElapsedMs;
  const response = await fetch(`${API_BASE}/agent/plan/resume`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`Backend error: ${response.status}`);
  }
  return response.json();
}

async function cancelAgentPlan(planId) {
  const response = await fetch(`${API_BASE}/agent/plan/cancel`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ plan_id: planId }),
  });
  if (!response.ok) {
    throw new Error(`Backend error: ${response.status}`);
  }
  return response.json();
}

/**
 * Controlled multi-step plan loop (Milestone 4B).
 * Backend owns progression; extension only executes the current approved action.
 */
async function runTaskPlanLoop(goal) {
  const tab = await getActiveTab();
  const tabMeta = { tabId: tab.id, windowId: tab.windowId };
  let { page, privacy_report } = await requestSanitizedSnapshot(tab.id);

  let stepResult = await callAgentPlan(
    goal,
    page,
    privacy_report,
    _latestVisualContext,
    _latestVisualUiMap,
    tabMeta
  );

  const planId = stepResult.plan_id || (stepResult.plan && stepResult.plan.plan_id);
  const sessionId =
    stepResult.session_id ||
    (stepResult.session && stepResult.session.session_id) ||
    null;
  if (sessionId || planId) {
    storeSessionContext(sessionId, planId, tab.id);
  }
  const maxIterations = 12;
  let iterations = 0;
  let lastResult = stepResult;
  let waitStartedAt = null;

  while (iterations < maxIterations) {
    iterations += 1;
    const status = stepResult.status;
    const planStatus =
      (stepResult.plan && stepResult.plan.status) || status;

    if (
      planStatus === 'completed' ||
      planStatus === 'failed' ||
      planStatus === 'cancelled' ||
      planStatus === 'unsupported' ||
      planStatus === 'paused' ||
      status === 'unsupported' ||
      status === 'cancelled' ||
      status === 'not_found' ||
      status === 'paused' ||
      status === 'max_replan_attempts_reached' ||
      stepResult.requires_user_intervention
    ) {
      lastResult = stepResult;
      break;
    }

    if (status === 'requires_confirmation' || status === 'waiting_for_confirmation') {
      if (stepResult.confirmation && stepResult.confirmation.id) {
        startApprovedActionPoll(tab.id, {
          confirmationId: stepResult.confirmation.id,
          lifecycleId: stepResult.lifecycle_id || null,
          task: goal,
          planId,
        });
      }
      lastResult = stepResult;
      break;
    }

    if (status === 'waiting' && stepResult.wait) {
      if (waitStartedAt == null) waitStartedAt = performance.now();
      const elapsed = Math.round(performance.now() - waitStartedAt);
      const pollMs = Math.max(100, Math.min(stepResult.wait.poll_ms || 400, 1000));
      await new Promise((r) => setTimeout(r, pollMs));
      ({ page, privacy_report } = await requestSanitizedSnapshot(tab.id));
      stepResult = await callAgentPlanResume(
        planId,
        page,
        privacy_report,
        _latestVisualContext,
        _latestVisualUiMap,
        tabMeta,
        elapsed
      );
      lastResult = stepResult;
      continue;
    }

    waitStartedAt = null;

    if (stepResult.action) {
      const actionMeta =
        (stepResult.action && stepResult.action.target) ||
        (stepResult.decision && stepResult.decision.resolved_target) ||
        {};
      const targetSelector =
        (stepResult.action && stepResult.action.selector) ||
        actionMeta.selector ||
        null;
      const preState =
        (await requestSafePageState(tab.id, targetSelector)) ||
        stepResult.pre_action_state ||
        null;
      const tExec0 = performance.now();
      const execution = await executeActionOnTab(tab.id, stepResult.action);
      const executionMs = Math.round(performance.now() - tExec0);
      if (execution && typeof execution === 'object') {
        execution.execution_time_ms =
          execution.execution_time_ms != null
            ? execution.execution_time_ms
            : executionMs;
      }
      const postState = await verifyActionResult(tab.id, targetSelector, 300);

      const historyItem = {
        timestamp: new Date().toISOString(),
        task: goal,
        plan_id: planId,
        privacy_redactions:
          (privacy_report && privacy_report.total_redactions) || 0,
        action: stepResult.action.type,
        status:
          execution && (execution.success || execution.status === 'success')
            ? 'success'
            : 'execution_failed',
        reason: stepResult.reason || null,
        strategy: actionMeta.strategy || null,
        source: actionMeta.source || stepResult.action.source || null,
        confidence: actionMeta.confidence != null ? actionMeta.confidence : null,
        execution,
        lifecycle_id: stepResult.lifecycle_id || null,
        tab_id: tab.id,
        pre_action_state: preState,
        post_action_state: postState,
        page,
        safety: stepResult.safety || null,
      };
      const report = await reportExecution(historyItem);
      lastResult = {
        ...stepResult,
        execution,
        report,
        plan: (report && report.plan) || stepResult.plan,
      };

      const nextPlanStatus =
        (report && report.plan && report.plan.status) ||
        (lastResult.plan && lastResult.plan.status);
      if (
        nextPlanStatus === 'completed' ||
        nextPlanStatus === 'failed' ||
        nextPlanStatus === 'cancelled'
      ) {
        break;
      }

      // Fresh perception for the next step — never reuse stale page/coords
      ({ page, privacy_report } = await requestSanitizedSnapshot(tab.id));
      stepResult = await callAgentPlanResume(
        planId,
        page,
        privacy_report,
        _latestVisualContext,
        _latestVisualUiMap,
        tabMeta
      );
      lastResult = stepResult;
      continue;
    }

    // Recovering / step_success without action → re-perceive and resume
    if (status === 'recovering' || status === 'step_success' || status === 'created') {
      ({ page, privacy_report } = await requestSanitizedSnapshot(tab.id));
      stepResult = await callAgentPlanResume(
        planId,
        page,
        privacy_report,
        _latestVisualContext,
        _latestVisualUiMap,
        tabMeta
      );
      lastResult = stepResult;
      continue;
    }

    lastResult = stepResult;
    break;
  }

  return {
    ...lastResult,
    plan_id: planId,
    privacy_report,
    iterations,
  };
}

/**
 * Full agent loop for one user task:
 *   sanitized snapshot → POST /agent/step → execute (if safe) → verify → report
 * Destructive actions return requires_confirmation and do not auto-execute.
 * Extension then polls for dashboard-approved action delivery.
 */
async function runAgentLoop(task) {
  const tab = await getActiveTab();
  const { page, privacy_report } = await requestSanitizedSnapshot(tab.id);

  const stepResult = await callAgentStep(
    task,
    page,
    privacy_report,
    _latestVisualContext,
    _latestVisualUiMap,
    { tabId: tab.id, windowId: tab.windowId }
  );

  let execution = null;
  let postState = null;
  let preState = stepResult.pre_action_state || null;
  const lifecycleId = stepResult.lifecycle_id || null;

  const actionMeta =
    (stepResult.action && stepResult.action.target) ||
    (stepResult.decision && stepResult.decision.resolved_target) ||
    {};

  const targetSelector =
    (stepResult.action && stepResult.action.selector) ||
    actionMeta.selector ||
    null;

  // Capture fresh pre-state locally when executing safe actions
  if (
    (stepResult.status === 'success' || stepResult.status === 'ready') &&
    stepResult.action
  ) {
    preState = (await requestSafePageState(tab.id, targetSelector)) || preState;
    const tExec0 = performance.now();
    execution = await executeActionOnTab(tab.id, stepResult.action);
    const executionMs = Math.round(performance.now() - tExec0);
    if (execution && typeof execution === 'object') {
      execution.execution_time_ms =
        execution.execution_time_ms != null
          ? execution.execution_time_ms
          : executionMs;
    }
    postState = await verifyActionResult(tab.id, targetSelector, 300);
  }

  // Start controlled polling only while a confirmation is pending for this tab
  if (
    stepResult.status === 'requires_confirmation' &&
    stepResult.confirmation &&
    stepResult.confirmation.id
  ) {
    startApprovedActionPoll(tab.id, {
      confirmationId: stepResult.confirmation.id,
      lifecycleId,
      task,
    });
  }

  const historyItem = {
    timestamp: new Date().toISOString(),
    task,
    privacy_redactions: (privacy_report && privacy_report.total_redactions) || 0,
    action: stepResult.action ? stepResult.action.type : null,
    status:
      stepResult.status === 'requires_confirmation'
        ? 'requires_confirmation'
        : execution && (execution.success || execution.status === 'success')
          ? 'success'
          : stepResult.status === 'no_action'
            ? 'no_action'
            : execution && execution.success === false
              ? 'execution_failed'
              : stepResult.status || 'unknown',
    reason: stepResult.reason || null,
    strategy:
      actionMeta.strategy ||
      (stepResult.action && stepResult.action.type === 'coordinate_click'
        ? 'coordinates'
        : null),
    source:
      actionMeta.source ||
      (stepResult.action && stepResult.action.source) ||
      null,
    confidence: actionMeta.confidence != null ? actionMeta.confidence : null,
    execution,
    lifecycle_id: lifecycleId,
    tab_id: tab.id,
    pre_action_state: preState,
    post_action_state: postState,
    safety: stepResult.safety || null,
    confirmation: stepResult.confirmation
      ? {
          required: true,
          id: stepResult.confirmation.id,
          approved: false,
          category: stepResult.confirmation.category,
        }
      : null,
  };

  // Only report execution for paths that already ran an action.
  // Confirmation path waits for dashboard approve → claim → execute.
  if (stepResult.status !== 'requires_confirmation') {
    await reportExecution(historyItem);
  }

  return {
    ...stepResult,
    privacy_report,
    visual_context: stepResult.visual_context || _latestVisualContext,
    visual_ui_map: stepResult.visual_ui_map || _latestVisualUiMap,
    execution,
    history_item: historyItem,
    tab_id: tab.id,
  };
}

/**
 * Legacy helper: dashboard is the confirmation authority.
 * Extension only claims via approved-action polling.
 */
async function runConfirmedAction(confirmationId) {
  // Do not call /confirm from the extension — dashboard owns approval.
  // Resume / start polling for the active tab matching this confirmation.
  const tab = await getActiveTab();
  startApprovedActionPoll(tab.id, {
    confirmationId,
    task: null,
  });
  setExecutionUiState('waiting_for_confirmation', {
    confirmationId,
    tabId: tab.id,
  });
  return {
    status: 'waiting_for_extension',
    confirmation_id: confirmationId,
    execution_status: 'waiting_for_extension',
    message: 'Polling for dashboard-approved action',
  };
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

/**
 * Capture → local face detect → unify → redact → POST only sanitized image.
 *
 * PRIVACY BOUNDARY ENFORCEMENT:
 * When privacyMode is enabled, the raw dataUrl from captureVisibleTab
 * is used ONLY as input to local face detection (boxes) + ScreenshotRedactor.
 * Only sanitizedDataUrl is transmitted to the dashboard storage endpoint.
 */
async function captureAndSanitizeScreenshot() {
  const privacyMode = await getPrivacyMode();
  const tab = await getActiveTab();

  const tDetect0 = performance.now();
  let boxes = [];
  let viewport = {
    width: 0,
    height: 0,
    devicePixelRatio: 1,
  };
  let detectionMetrics = {};

  try {
    const regionInfo = await requestSensitiveBoxes(tab.id);
    boxes = regionInfo.boxes;
    viewport = regionInfo.viewport;
    detectionMetrics = regionInfo.metrics || {};
  } catch (_) {
    // If content script is unavailable, still capture but redact nothing
    // (or refuse to send raw when privacy is on — prefer empty boxes).
  }
  const detectionMs =
    detectionMetrics.dom_sensitive_detection_ms != null
      ? detectionMetrics.dom_sensitive_detection_ms
      : Math.round(performance.now() - tDetect0);

  const tCapture0 = performance.now();
  // RAW screenshot — local only when privacy mode is on
  const rawDataUrl = await captureVisibleTab();
  const captureMs = Math.round(performance.now() - tCapture0);

  let imageToSend = rawDataUrl;
  let screenshotPrivacyReport = null;
  let redactMetrics = {};
  let localOnlyOriginal = null;
  let visualContext = null;
  let faceDetectionMs = 0;
  let faceBoxCount = 0;

  if (privacyMode) {
    // --- PRIVACY BOUNDARY: raw stays in this block; only sanitized exits ---

    // 1) Local face detection → bounding boxes only (no crops/embeddings)
    const faceResult = await self.FaceDetector.detectFaces(rawDataUrl, FACE_CONFIG);
    faceDetectionMs = faceResult.face_detection_ms || 0;
    const faceBoxes = faceResult.faces || [];
    faceBoxCount = faceBoxes.length;

    // 2) Unify DOM PII boxes + face boxes
    const unified = self.FaceDetector.mergeSensitiveRegions(boxes, faceBoxes);

    // 3) Local Canvas redaction (PII + faces)
    const result = await self.ScreenshotRedactor.redactScreenshot(
      rawDataUrl,
      unified,
      viewport,
      { faceConfig: FACE_CONFIG }
    );
    imageToSend = result.sanitizedDataUrl;
    screenshotPrivacyReport = result.report;
    redactMetrics = result.metrics || {};
    localOnlyOriginal = rawDataUrl;

    // --- END PRIVACY BOUNDARY ---
  }

  const timing = {
    screenshot_capture_ms: captureMs,
    dom_sensitive_detection_ms: detectionMs,
    face_detection_ms: faceDetectionMs,
    face_box_processing_ms: 0,
    coordinate_mapping_ms: redactMetrics.coordinate_mapping_ms || 0,
    image_redaction_ms: redactMetrics.image_redaction_ms || 0,
    total_privacy_processing_ms:
      (redactMetrics.total_privacy_processing_ms || 0) +
      faceDetectionMs +
      detectionMs,
  };

  if (screenshotPrivacyReport) {
    screenshotPrivacyReport.timing = timing;
    if (!screenshotPrivacyReport.vision) {
      screenshotPrivacyReport.vision = {
        faces_detected: faceBoxCount,
        faces_redacted: faceBoxCount,
      };
    }
    visualContext = self.FaceDetector.buildVisualContext(
      screenshotPrivacyReport,
      timing
    );
    _latestVisualContext = visualContext;
  }

  // DOM geometry for Visual UI Map (sanitized snapshot — no raw PII values)
  let domElements = [];
  try {
    const snap = await requestSanitizedSnapshot(tab.id);
    domElements = (snap.page && snap.page.elements) || [];
  } catch (_) {
    domElements = [];
  }

  // Build Visual UI Map from SANITIZED image + DOM geometry (local server)
  let visualUiMap = null;
  try {
    const uiRes = await fetch(`${API_BASE}/vision/ui-map`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image: imageToSend,
        sanitized: privacyMode === true,
        privacy_mode: privacyMode,
        dom_elements: domElements,
        sensitive_regions: (screenshotPrivacyReport && screenshotPrivacyReport.redactions) || [],
      }),
    });
    if (uiRes.ok) {
      const uiJson = await uiRes.json();
      if (uiJson.privacy_safe && uiJson.visual_ui_map) {
        visualUiMap = uiJson.visual_ui_map;
        _latestVisualUiMap = visualUiMap;
      } else {
        _latestVisualUiMap = null;
      }
    }
  } catch (_) {
    // Non-fatal: agent can continue with DOM-only perception
    _latestVisualUiMap = null;
  }

  // NETWORK: only sanitized (or raw if privacy explicitly disabled)
  await fetch(`${API_BASE}/agent/screenshot`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      image: imageToSend,
      sanitized: privacyMode === true,
      privacy_mode: privacyMode,
      screenshot_privacy_report: screenshotPrivacyReport,
      visual_context: visualContext,
      visual_ui_map: visualUiMap || undefined,
      dom_elements: domElements,
      // Explicit: never include raw image field
    }),
  });

  return {
    // For popup: sanitized is what was sent; original marked local-only
    dataUrl: imageToSend,
    sanitized: privacyMode,
    localOnlyOriginal: privacyMode ? localOnlyOriginal : null,
    screenshot_privacy_report: screenshotPrivacyReport,
    visual_context: visualContext,
    visual_ui_map: visualUiMap,
    timing,
    box_count: boxes.length + faceBoxCount,
  };
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  switch (message.type) {
    case 'GET_STATUS':
      fetchAgentStatus()
        .then((data) => {
          chrome.storage.local.get(
            {
              executionUiState: 'idle',
              confirmationId: null,
              executionId: null,
            },
            (local) => {
              sendResponse({
                ok: true,
                data: {
                  ...data,
                  execution_ui: {
                    state: local.executionUiState || 'idle',
                    confirmation_id: local.confirmationId || null,
                    execution_id: local.executionId || null,
                  },
                },
              });
            }
          );
        })
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;

    case 'ACTION_EXECUTE':
    case 'RUN_AGENT_STEP':
      runAgentLoop(message.task || message.goal)
        .then((data) => sendResponse({ ok: true, data }))
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;

    case 'RUN_TASK_PLAN':
      runTaskPlanLoop(message.goal || message.task)
        .then((data) => sendResponse({ ok: true, data }))
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;

    case 'CANCEL_TASK_PLAN':
      cancelAgentPlan(message.plan_id || message.planId)
        .then((data) => {
          if (message.tabId != null) {
            stopApprovedActionPoll(message.tabId);
          }
          sendResponse({ ok: true, data });
        })
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;

    case 'CONFIRM_AND_EXECUTE':
      runConfirmedAction(message.confirmation_id || message.confirmationId)
        .then((data) => sendResponse({ ok: true, data }))
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;

    case 'STOP_APPROVED_POLL':
      if (message.tabId != null) {
        stopApprovedActionPoll(message.tabId);
      }
      sendResponse({ ok: true });
      return false;

    case 'CAPTURE_REQUEST':
      captureAndSanitizeScreenshot()
        .then((result) =>
          sendResponse({
            ok: true,
            dataUrl: result.dataUrl,
            sanitized: result.sanitized,
            // localOnlyOriginal is intentionally NOT forwarded to reduce
            // accidental leakage via message ports; popup can request
            // LOCAL_ORIGINAL_PREVIEW separately if needed.
            screenshot_privacy_report: result.screenshot_privacy_report,
            visual_context: result.visual_context,
            visual_ui_map: result.visual_ui_map,
            timing: result.timing,
            box_count: result.box_count,
          })
        )
        .catch((err) => sendResponse({ ok: false, error: err.message }));
      return true;

    default:
      sendResponse({ ok: false, error: 'unknown_message_type' });
  }
});
