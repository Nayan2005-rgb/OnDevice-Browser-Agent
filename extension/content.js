// Content script: extracts a sanitized DOM snapshot and executes
// browser actions. Relays only privacy-filtered data to the background.

(function () {
  function getSanitizedSnapshot() {
    // PRIVACY BOUNDARY: snapshot is already sanitized by privacyFilter.js
    if (window.__privacyFilter && window.__privacyFilter.buildSanitizedSnapshot) {
      return window.__privacyFilter.buildSanitizedSnapshot();
    }
    return {
      page: {
        url: window.location.href,
        title: document.title,
        visibleText: '',
        elements: [],
      },
      privacy_report: {
        emails_detected: 0,
        phones_detected: 0,
        password_fields_detected: 0,
        cards_detected: 0,
        ssn_detected: 0,
        total_redactions: 0,
      },
    };
  }

  function getSafePageState(targetSelector) {
    if (window.__privacyFilter && window.__privacyFilter.buildSafePageState) {
      return window.__privacyFilter.buildSafePageState(targetSelector);
    }
    return {
      url: window.location.href,
      visible_elements: [],
      elements: [],
      element_count: 0,
      dialog_present: false,
      scroll_y: Math.round(window.scrollY || 0),
      target_present: null,
      target_enabled: null,
      capture_ms: 0,
    };
  }

  function executeAction(action) {
    if (!action || !window.__browserActions) {
      return { success: false, error: 'Browser actions unavailable' };
    }

    const type = (action.type || '').toLowerCase();
    const actions = window.__browserActions;

    try {
      if (type === 'click') {
        return actions.click(action.selector);
      }
      if (type === 'coordinate_click') {
        const x =
          action.x != null
            ? action.x
            : action.coordinates && action.coordinates.x;
        const y =
          action.y != null
            ? action.y
            : action.coordinates && action.coordinates.y;
        return actions.clickAtCoordinates(x, y);
      }
      if (type === 'type') {
        return actions.type(action.selector, action.text || '');
      }
      if (type === 'scroll') {
        return actions.scroll(
          action.direction || 'down',
          action.amount != null ? action.amount : 500
        );
      }
      if (type === 'navigate') {
        return actions.navigate(action.url || action.href || '');
      }
      return { success: false, error: `Unsupported action: ${type}` };
    } catch (err) {
      return { success: false, error: err.message || String(err) };
    }
  }

  function getSensitiveRegions() {
    if (
      window.__privacyFilter &&
      window.__privacyFilter.getSensitiveBoundingBoxes
    ) {
      return window.__privacyFilter.getSensitiveBoundingBoxes();
    }
    return {
      boxes: [],
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight,
        devicePixelRatio: window.devicePixelRatio || 1,
      },
      metrics: { dom_sensitive_detection_ms: 0 },
    };
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'GET_DOM_SNAPSHOT') {
      sendResponse({ ok: true, ...getSanitizedSnapshot() });
      return true;
    }

    if (message.type === 'GET_SENSITIVE_BOXES') {
      // Boxes only — no raw sensitive text values
      sendResponse({ ok: true, ...getSensitiveRegions() });
      return true;
    }

    if (message.type === 'GET_SAFE_PAGE_STATE') {
      const state = getSafePageState(message.targetSelector || message.selector);
      sendResponse({ ok: true, state });
      return true;
    }

    if (message.type === 'EXECUTE_ACTION') {
      const result = executeAction(message.action);
      sendResponse(result);
      return true;
    }

    if (message.type === 'VERIFY_ACTION_RESULT') {
      const delay = Math.max(
        0,
        Math.min(Number(message.delay_ms) || 300, 2000)
      );
      sleep(delay).then(() => {
        const state = getSafePageState(message.targetSelector || message.selector);
        sendResponse({ ok: true, state, delay_ms: delay });
      });
      return true;
    }

    return false;
  });
})();
