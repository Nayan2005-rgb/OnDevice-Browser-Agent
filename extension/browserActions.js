// Executes concrete browser actions (click, type, scroll, navigate)
// requested by the agent's action_planner.

const BrowserActions = {
  click(selector) {
    const el = document.querySelector(selector);
    if (el) el.click();
    return !!el;
  },

  type(selector, text) {
    const el = document.querySelector(selector);
    if (!el) return false;
    el.focus();
    el.value = text;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  },

  scrollTo(selector) {
    const el = document.querySelector(selector);
    if (!el) return false;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return true;
  },
};

window.__browserActions = BrowserActions;
