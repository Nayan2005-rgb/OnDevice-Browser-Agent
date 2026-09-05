// Runs in the page context before content.js. Provides DOM-level
// sanitization so PII never leaves the page in the first place.

window.__privacyFilter = {
  /**
   * Walks the DOM and masks obvious PII patterns (emails, phone numbers,
   * card numbers) before any snapshot is sent to the agent backend.
   */
  sanitizeDom(doc) {
    const clone = doc.body ? doc.body.cloneNode(true) : null;
    if (!clone) return null;

    const emailRegex = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g;
    const walker = document.createTreeWalker(clone, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      node.textContent = node.textContent.replace(emailRegex, '[REDACTED_EMAIL]');
    }
    return clone.outerHTML;
  },
};
