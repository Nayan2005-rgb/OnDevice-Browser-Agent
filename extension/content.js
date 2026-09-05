// Content script: observes the DOM and relays sanitized page state
// to the background script for the agent's perception loop.

(function () {
  function getSanitizedSnapshot() {
    // privacyFilter.js is expected to expose window.__privacyFilter
    if (window.__privacyFilter) {
      return window.__privacyFilter.sanitizeDom(document);
    }
    return null;
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'GET_DOM_SNAPSHOT') {
      sendResponse({ snapshot: getSanitizedSnapshot() });
    }
    return true;
  });
})();
