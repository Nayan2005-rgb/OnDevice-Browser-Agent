// Executes concrete browser actions (click, type, scroll, coordinate_click)
// requested by the agent's action planner via the content script.

const BrowserActions = {
  click(selector) {
    const el = document.querySelector(selector);
    if (!el) {
      return { success: false, error: 'Element not found', action: 'click', target: selector };
    }
    if (_isUnsafeElement(el)) {
      return {
        success: false,
        status: 'failed',
        error: 'unsafe_element',
        reason: 'sensitive_or_password_target',
        action: 'click',
        target: selector,
      };
    }
    el.focus();
    el.dispatchEvent(
      new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window })
    );
    el.dispatchEvent(
      new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window })
    );
    el.click();
    return { success: true, status: 'success', action: 'click', target: selector };
  },

  type(selector, text) {
    const el = document.querySelector(selector);
    if (!el) {
      return { success: false, error: 'Element not found', action: 'type', target: selector };
    }
    if (_isUnsafeElement(el)) {
      return {
        success: false,
        status: 'failed',
        error: 'unsafe_element',
        reason: 'sensitive_or_password_target',
        action: 'type',
        target: selector,
      };
    }
    el.focus();
    el.value = text;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return { success: true, status: 'success', action: 'type', target: selector };
  },

  scroll(direction, amount) {
    const delta = Number(amount) || 500;
    const y = String(direction).toLowerCase() === 'up' ? -delta : delta;
    window.scrollBy({ top: y, left: 0, behavior: 'smooth' });
    return {
      success: true,
      status: 'success',
      action: 'scroll',
      target: direction || 'down',
      amount: delta,
    };
  },

  /**
   * Safe same-tab navigation for allowlisted local / demo URLs only.
   * Absolute http(s) to non-local hosts is rejected client-side as defense in depth.
   */
  navigate(url) {
    const raw = String(url || '').trim();
    if (!raw) {
      return { success: false, status: 'failed', error: 'missing_url', action: 'navigate' };
    }
    const lower = raw.toLowerCase();
    const isRelative =
      !/^https?:\/\//i.test(raw) && !raw.startsWith('//') && !/^javascript:/i.test(lower);
    let allow = isRelative || lower.startsWith('file:');
    if (!allow && /^https?:\/\//i.test(raw)) {
      try {
        const u = new URL(raw, window.location.href);
        const host = (u.hostname || '').toLowerCase();
        allow =
          host === 'localhost' ||
          host === '127.0.0.1' ||
          host.endsWith('.localhost') ||
          host.includes('example.com');
      } catch (_) {
        allow = false;
      }
    }
    if (!allow) {
      return {
        success: false,
        status: 'failed',
        error: 'unsafe_navigation',
        action: 'navigate',
        target: raw,
      };
    }
    try {
      window.location.assign(raw);
      return { success: true, status: 'success', action: 'navigate', target: raw };
    } catch (err) {
      return {
        success: false,
        status: 'failed',
        error: err && err.message ? err.message : 'navigate_failed',
        action: 'navigate',
      };
    }
  },

  scrollTo(selector) {
    const el = document.querySelector(selector);
    if (!el) {
      return { success: false, error: 'Element not found', action: 'scrollTo', target: selector };
    }
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return { success: true, status: 'success', action: 'scrollTo', target: selector };
  },

  /**
   * Click at CSS viewport coordinates after safety validation.
   * Coordinates must already be mapped from screenshot → CSS space.
   */
  clickAtCoordinates(x, y) {
    const start = performance.now();
    const cx = Number(x);
    const cy = Number(y);

    if (!Number.isFinite(cx) || !Number.isFinite(cy)) {
      return {
        success: false,
        status: 'failed',
        reason: 'invalid_coordinates',
        action: 'coordinate_click',
        coordinates: { x, y },
        execution_time_ms: Math.round(performance.now() - start),
      };
    }

    const vw = window.innerWidth;
    const vh = window.innerHeight;
    if (cx < 0 || cy < 0 || cx >= vw || cy >= vh) {
      return {
        success: false,
        status: 'failed',
        reason: 'outside_viewport',
        action: 'coordinate_click',
        coordinates: { x: cx, y: cy },
        execution_time_ms: Math.round(performance.now() - start),
      };
    }

    let el = document.elementFromPoint(cx, cy);
    if (!el) {
      return {
        success: false,
        status: 'failed',
        reason: 'no_element_at_coordinates',
        action: 'coordinate_click',
        coordinates: { x: Math.round(cx), y: Math.round(cy) },
        target_found: false,
        execution_time_ms: Math.round(performance.now() - start),
      };
    }

    // Scroll into view and re-query if needed
    if (typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
      const again = document.elementFromPoint(cx, cy);
      if (again) el = again;
    }

    if (_isUnsafeElement(el) || _isRedactedRegion(el)) {
      return {
        success: false,
        status: 'failed',
        reason: 'unsafe_element',
        action: 'coordinate_click',
        coordinates: { x: Math.round(cx), y: Math.round(cy) },
        target_found: true,
        target: _describeElement(el),
        execution_time_ms: Math.round(performance.now() - start),
      };
    }

    if (el.disabled || el.getAttribute('aria-disabled') === 'true') {
      return {
        success: false,
        status: 'failed',
        reason: 'disabled_element',
        action: 'coordinate_click',
        coordinates: { x: Math.round(cx), y: Math.round(cy) },
        target_found: true,
        target: _describeElement(el),
        execution_time_ms: Math.round(performance.now() - start),
      };
    }

    if (!_isVisible(el)) {
      return {
        success: false,
        status: 'failed',
        reason: 'invisible_element',
        action: 'coordinate_click',
        coordinates: { x: Math.round(cx), y: Math.round(cy) },
        target_found: true,
        target: _describeElement(el),
        execution_time_ms: Math.round(performance.now() - start),
      };
    }

    _dispatchRealisticClick(el, cx, cy);

    return {
      success: true,
      status: 'success',
      action: 'coordinate_click',
      strategy: 'coordinates',
      coordinates: { x: Math.round(cx), y: Math.round(cy) },
      target_found: true,
      target: _describeElement(el),
      element_tag: el.tagName,
      execution_time_ms: Math.round(performance.now() - start),
    };
  },
};

function _dispatchRealisticClick(el, x, y) {
  const opts = {
    bubbles: true,
    cancelable: true,
    view: window,
    clientX: x,
    clientY: y,
  };
  try {
    el.dispatchEvent(new PointerEvent('pointerdown', { ...opts, pointerId: 1, pointerType: 'mouse' }));
  } catch (_) {
    /* PointerEvent may be unavailable in older environments */
  }
  el.dispatchEvent(new MouseEvent('mousedown', opts));
  try {
    el.dispatchEvent(new PointerEvent('pointerup', { ...opts, pointerId: 1, pointerType: 'mouse' }));
  } catch (_) {
    /* ignore */
  }
  el.dispatchEvent(new MouseEvent('mouseup', opts));
  el.dispatchEvent(new MouseEvent('click', opts));
  if (typeof el.focus === 'function') {
    try {
      el.focus();
    } catch (_) {
      /* ignore */
    }
  }
}

function _describeElement(el) {
  return {
    tag: el.tagName,
    role: el.getAttribute('role') || undefined,
    id: el.id || undefined,
    type: el.type || undefined,
  };
}

function _isUnsafeElement(el) {
  if (!el || !el.tagName) return true;
  const tag = el.tagName.toLowerCase();
  const type = (el.type || '').toLowerCase();
  const name = (el.name || '').toLowerCase();
  const id = (el.id || '').toLowerCase();
  const autocomplete = (el.getAttribute('autocomplete') || '').toLowerCase();
  const role = (el.getAttribute('role') || '').toLowerCase();
  const dataSensitive = el.getAttribute('data-sensitive');
  const aria = (el.getAttribute('aria-label') || '').toLowerCase();

  if (type === 'password') return true;
  if (dataSensitive === 'true' || dataSensitive === '1') return true;
  if (role === 'password') return true;
  if (el.closest && el.closest('[data-redacted="true"], [data-sensitive="true"]')) {
    return true;
  }

  const blob = `${name} ${id} ${autocomplete} ${aria}`;
  const blocked = [
    'password',
    'credit',
    'cardnumber',
    'card-number',
    'cc-number',
    'cvv',
    'cvc',
    'ssn',
    'social-security',
  ];
  if (blocked.some((k) => blob.includes(k))) return true;

  // Credit-card-like input types
  if (type === 'tel' && /card|cvv|cvc/.test(blob)) return true;
  if (tag === 'input' && /cc-|card/.test(autocomplete)) return true;

  return false;
}

function _isRedactedRegion(el) {
  if (!el) return false;
  if (el.getAttribute && el.getAttribute('data-redacted') === 'true') return true;
  if (el.classList && (el.classList.contains('redacted') || el.classList.contains('privacy-redacted'))) {
    return true;
  }
  return !!(el.closest && el.closest('[data-redacted="true"]'));
}

function _isVisible(el) {
  if (!el) return false;
  const style = window.getComputedStyle(el);
  if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
    return false;
  }
  const rect = el.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

window.__browserActions = BrowserActions;
