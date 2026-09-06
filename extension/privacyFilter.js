// Runs in the page context before content.js.
// PRIVACY BOUNDARY: All sanitization happens HERE on-device.
// Raw PII must never leave this filter toward the network.

window.__privacyFilter = (function () {
  // --- PII patterns (mirrored from privacy/pii_detector.py) ---
  const EMAIL_RE = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g;
  const PHONE_RE =
    /\b(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b/g;
  const SSN_RE = /\b\d{3}-\d{2}-\d{4}\b/g;
  const CARD_RE = /\b(?:\d[ -]*?){13,16}\b/g;

  const SENSITIVE_KEYWORDS = [
    'password',
    'passwd',
    'pwd',
    'ssn',
    'social security',
    'credit card',
    'card number',
    'cvv',
    'cvc',
    'passport',
    'secret',
  ];

  const INTERACTIVE_SELECTOR =
    'a[href], button, input, textarea, select, [role="button"], [role="link"], [contenteditable="true"], [onclick]';

  function rectToBox(rect) {
    return {
      x: Math.round(rect.left),
      y: Math.round(rect.top),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    };
  }

  function mapElementType(el) {
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    const inputType = (el.getAttribute('type') || '').toLowerCase();
    if (role === 'button' || tag === 'button' || inputType === 'submit' || inputType === 'button') {
      return 'button';
    }
    if (role === 'link' || tag === 'a') return 'link';
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable) {
      return 'input';
    }
    if (tag === 'img') return 'image';
    if (role === 'dialog' || tag === 'dialog') return 'dialog';
    return 'unknown';
  }

  function isVisible(el) {
    if (!el || el.disabled) return false;
    const style = window.getComputedStyle(el);
    if (
      style.display === 'none' ||
      style.visibility === 'hidden' ||
      style.opacity === '0'
    ) {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function cssEscape(value) {
    if (window.CSS && typeof CSS.escape === 'function') {
      return CSS.escape(value);
    }
    return String(value).replace(/([ !"#$%&'()*+,./:;<=>?@[\\\]^`{|}~])/g, '\\$1');
  }

  /**
   * Build a reasonably stable CSS selector for an element.
   */
  function buildSelector(el) {
    if (el.id) {
      return `#${cssEscape(el.id)}`;
    }

    const name = el.getAttribute('name');
    if (name) {
      const tag = el.tagName.toLowerCase();
      const candidate = `${tag}[name="${cssEscape(name)}"]`;
      try {
        if (document.querySelectorAll(candidate).length === 1) {
          return candidate;
        }
      } catch (_) {
        /* ignore invalid selector */
      }
    }

    const aria = el.getAttribute('aria-label');
    if (aria) {
      const tag = el.tagName.toLowerCase();
      const candidate = `${tag}[aria-label="${cssEscape(aria)}"]`;
      try {
        if (document.querySelectorAll(candidate).length === 1) {
          return candidate;
        }
      } catch (_) {
        /* ignore */
      }
    }

    // Fallback: short path from a unique ancestor
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.body) {
      let part = node.tagName.toLowerCase();
      if (node.id) {
        parts.unshift(`#${cssEscape(node.id)}`);
        break;
      }
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(
          (c) => c.tagName === node.tagName
        );
        if (siblings.length > 1) {
          const index = siblings.indexOf(node) + 1;
          part += `:nth-of-type(${index})`;
        }
      }
      parts.unshift(part);
      node = parent;
      if (parts.length >= 5) break;
    }
    return parts.join(' > ');
  }

  function getVisibleText(el) {
    const text =
      el.innerText ||
      el.textContent ||
      el.value ||
      el.getAttribute('aria-label') ||
      el.getAttribute('placeholder') ||
      '';
    return String(text).replace(/\s+/g, ' ').trim().slice(0, 200);
  }

  function collectPageVisibleText() {
    const body = document.body;
    if (!body) return '';
    return (body.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 4000);
  }

  function fieldLooksSensitive(el) {
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'password') return 'password';

    const attrs = [
      type,
      el.getAttribute('name') || '',
      el.id || '',
      el.getAttribute('autocomplete') || '',
      el.getAttribute('placeholder') || '',
      el.getAttribute('aria-label') || '',
    ]
      .join(' ')
      .toLowerCase();
    const attrsNorm = attrs.replace(/[_-]/g, ' ');

    for (const kw of SENSITIVE_KEYWORDS) {
      if (attrs.includes(kw) || attrsNorm.includes(kw)) {
        if (kw.includes('password') || kw === 'passwd' || kw === 'pwd' || kw === 'secret') {
          return 'password';
        }
        if (kw.includes('card') || kw === 'cvv' || kw === 'cvc') {
          return 'credit_card';
        }
        if (kw.includes('ssn') || kw.includes('social')) {
          return 'ssn';
        }
        return 'sensitive';
      }
    }

    if (attrsNorm.includes('card') || attrsNorm.includes('cc number')) {
      return 'credit_card';
    }

    if (type === 'email' || attrs.includes('email')) return 'email';
    if (type === 'tel' || attrs.includes('phone') || attrs.includes('tel')) {
      return 'phone';
    }
    return null;
  }

  function redactText(text, report) {
    if (!text) return text;
    let out = text;

    out = out.replace(EMAIL_RE, () => {
      report.emails_detected += 1;
      report.total_redactions += 1;
      return '[EMAIL_REDACTED]';
    });

    out = out.replace(SSN_RE, () => {
      report.ssn_detected += 1;
      report.total_redactions += 1;
      return '[SSN_REDACTED]';
    });

    out = out.replace(CARD_RE, (match) => {
      const digits = match.replace(/\D/g, '');
      if (digits.length < 13 || digits.length > 16) return match;
      report.cards_detected += 1;
      report.total_redactions += 1;
      return '[CARD_REDACTED]';
    });

    out = out.replace(PHONE_RE, () => {
      report.phones_detected += 1;
      report.total_redactions += 1;
      return '[PHONE_REDACTED]';
    });

    return out;
  }

  function rectToBox(rect) {
    return {
      x: Math.round(rect.left),
      y: Math.round(rect.top),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
    };
  }

  function isBoxInViewport(box) {
    if (!box || box.width <= 0 || box.height <= 0) return false;
    const vw = window.innerWidth || 0;
    const vh = window.innerHeight || 0;
    return box.x < vw && box.y < vh && box.x + box.width > 0 && box.y + box.height > 0;
  }

  function classifyTextCategory(match, sourceRe) {
    if (sourceRe === EMAIL_RE) return 'email';
    if (sourceRe === PHONE_RE) return 'phone';
    if (sourceRe === SSN_RE) return 'ssn';
    if (sourceRe === CARD_RE) return 'credit_card';
    return 'sensitive';
  }

  /**
   * Collect viewport bounding boxes for visible sensitive elements and
   * text-node PII matches. Never includes raw sensitive values.
   */
  function getSensitiveBoundingBoxes() {
    const t0 = performance.now();
    const boxes = [];
    const seenKeys = new Set();

    function pushBox(category, rect) {
      const box = rectToBox(rect);
      if (!isBoxInViewport(box)) return;
      const key = `${category}:${box.x},${box.y},${box.width},${box.height}`;
      if (seenKeys.has(key)) return;
      seenKeys.add(key);
      boxes.push({
        category,
        x: box.x,
        y: box.y,
        width: box.width,
        height: box.height,
      });
    }

    // 1) Sensitive form / interactive fields
    document.querySelectorAll(INTERACTIVE_SELECTOR).forEach((el) => {
      if (!isVisible(el)) return;
      const kind = fieldLooksSensitive(el);
      if (!kind) return;
      pushBox(kind, el.getBoundingClientRect());
    });

    // 2) Text nodes containing detected PII (email/phone/ssn/card)
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const patterns = [EMAIL_RE, SSN_RE, CARD_RE, PHONE_RE];
    let textNode;
    while ((textNode = walker.nextNode())) {
      const parent = textNode.parentElement;
      if (!parent || !isVisible(parent)) continue;
      const tag = parent.tagName;
      if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT') continue;

      const raw = textNode.nodeValue || '';
      if (!raw.trim()) continue;

      for (const re of patterns) {
        re.lastIndex = 0;
        let match;
        while ((match = re.exec(raw)) !== null) {
          if (re === CARD_RE) {
            const digits = match[0].replace(/\D/g, '');
            if (digits.length < 13 || digits.length > 16) continue;
          }
          try {
            const range = document.createRange();
            range.setStart(textNode, match.index);
            range.setEnd(textNode, match.index + match[0].length);
            pushBox(classifyTextCategory(match[0], re), range.getBoundingClientRect());
            range.detach();
          } catch (_) {
            // Range may fail on some nodes; skip without exposing values
          }
        }
      }
    }

    const detectionMs = Math.round(performance.now() - t0);
    return {
      boxes,
      viewport: {
        width: window.innerWidth,
        height: window.innerHeight,
        devicePixelRatio: window.devicePixelRatio || 1,
      },
      metrics: {
        dom_sensitive_detection_ms: detectionMs,
      },
    };
  }

  function extractElements() {
    const nodes = document.querySelectorAll(INTERACTIVE_SELECTOR);
    const elements = [];
    const seen = new Set();

    nodes.forEach((el) => {
      if (!isVisible(el)) return;

      const selector = buildSelector(el);
      if (!selector || seen.has(selector)) return;
      seen.add(selector);

      const tag = el.tagName.toLowerCase();
      const sensitiveKind = fieldLooksSensitive(el);
      const rect = el.getBoundingClientRect();
      const box = rectToBox(rect);
      const role = el.getAttribute('role') || el.getAttribute('type') || tag;
      const uiType = mapElementType(el);

      // Never include raw values from password / sensitive inputs in geometry path
      let value = null;
      if (tag === 'input' || tag === 'textarea') {
        if (sensitiveKind || (el.getAttribute('type') || '').toLowerCase() === 'password') {
          value = null;
        } else {
          value = el.value || '';
        }
      }

      elements.push({
        tag,
        text: getVisibleText(el),
        selector,
        type: el.getAttribute('type'),
        name: el.getAttribute('name'),
        id: el.id || null,
        placeholder: el.getAttribute('placeholder'),
        ariaLabel: el.getAttribute('aria-label'),
        autocomplete: el.getAttribute('autocomplete'),
        value,
        role,
        uiType,
        box,
        interactive: true,
        source: 'dom',
        confidence: 1.0,
        sensitive: !!sensitiveKind,
        sensitiveKind: sensitiveKind,
      });
    });

    return elements.slice(0, 80);
  }

  /**
   * Extract a structured page snapshot, then sanitize it in-place.
   * Returns { page, privacy_report }. Raw PII is stripped before return.
   */
  function buildSanitizedSnapshot() {
    // PRIVACY BOUNDARY START — raw page data exists only inside this function
    const report = {
      emails_detected: 0,
      phones_detected: 0,
      password_fields_detected: 0,
      cards_detected: 0,
      ssn_detected: 0,
      total_redactions: 0,
    };

    const rawElements = extractElements();
    const rawVisibleText = collectPageVisibleText();

    const elements = rawElements.map((el) => {
      const sanitized = { ...el };

      if (el.sensitiveKind === 'password' || el.type === 'password') {
        report.password_fields_detected += 1;
        report.total_redactions += 1;
        sanitized.value = '[PASSWORD_REDACTED]';
        sanitized.text = sanitized.text
          ? redactText(sanitized.text, report)
          : '[PASSWORD_REDACTED]';
        sanitized.sensitive = true;
      } else if (el.sensitiveKind === 'email' || el.type === 'email') {
        if (sanitized.value) {
          sanitized.value = redactText(sanitized.value, report);
        }
        sanitized.text = redactText(sanitized.text || '', report);
        sanitized.sensitive = true;
      } else if (el.sensitiveKind === 'phone' || el.type === 'tel') {
        if (sanitized.value) {
          sanitized.value = redactText(sanitized.value, report);
        }
        sanitized.text = redactText(sanitized.text || '', report);
        sanitized.sensitive = true;
      } else if (el.sensitiveKind === 'credit_card' || el.sensitiveKind === 'ssn') {
        if (sanitized.value) {
          sanitized.value = `[${el.sensitiveKind.toUpperCase()}_REDACTED]`;
          report.total_redactions += 1;
          if (el.sensitiveKind === 'credit_card') report.cards_detected += 1;
          if (el.sensitiveKind === 'ssn') report.ssn_detected += 1;
        }
        sanitized.text = redactText(sanitized.text || '', report);
        sanitized.sensitive = true;
      } else {
        sanitized.text = redactText(sanitized.text || '', report);
        if (sanitized.value) {
          sanitized.value = redactText(sanitized.value, report);
        }
      }

      // Never ship raw password-like values even if detection missed
      if ((sanitized.type || '').toLowerCase() === 'password') {
        sanitized.value = '[PASSWORD_REDACTED]';
      }

      delete sanitized.sensitiveKind;
      return sanitized;
    });

    const visibleText = redactText(rawVisibleText, report);

    const page = {
      url: window.location.href,
      title: redactText(document.title || '', report),
      visibleText,
      elements,
    };
    // PRIVACY BOUNDARY END — only sanitized `page` + `report` leave here

    return { page, privacy_report: report };
  }

  /**
   * Safe page state for pre/post action verification.
   * Only sanitized labels, roles, counts — never raw values or screenshots.
   */
  function buildSafePageState(targetSelector) {
    const t0 = performance.now();
    const snapshot = buildSanitizedSnapshot();
    const elements = (snapshot.page && snapshot.page.elements) || [];
    const visible_elements = elements.map((el) => ({
      tag: el.tag || '',
      role: el.role || '',
      type: el.type || '',
      label: el.sensitive ? '[REDACTED]' : (el.text || el.ariaLabel || '').slice(0, 40),
      selector: el.sensitive ? undefined : el.selector,
      sensitive: !!el.sensitive,
    }));

    let dialog_present = false;
    try {
      dialog_present = !!(
        document.querySelector('dialog[open], [role="dialog"], [role="alertdialog"]') ||
        visible_elements.some(
          (e) => e.role === 'dialog' || e.tag === 'dialog'
        )
      );
    } catch (_) {
      dialog_present = false;
    }

    let target_present = null;
    let target_enabled = null;
    if (targetSelector) {
      try {
        const el = document.querySelector(targetSelector);
        target_present = !!el;
        target_enabled = el ? !el.disabled : null;
      } catch (_) {
        target_present = false;
        target_enabled = null;
      }
    }

    return {
      url: (snapshot.page && snapshot.page.url) || window.location.href,
      visible_elements,
      elements: visible_elements,
      element_count: visible_elements.length,
      dialog_present,
      scroll_y: Math.round(window.scrollY || window.pageYOffset || 0),
      target_present,
      target_enabled,
      capture_ms: Math.round(performance.now() - t0),
    };
  }

  /**
   * Legacy helper kept for callers expecting HTML string output.
   * Prefer buildSanitizedSnapshot() for the agent loop.
   */
  function sanitizeDom(doc) {
    const snapshot = buildSanitizedSnapshot();
    return snapshot.page.visibleText;
  }

  return {
    sanitizeDom,
    buildSanitizedSnapshot,
    buildSafePageState,
    getSensitiveBoundingBoxes,
  };
})();
