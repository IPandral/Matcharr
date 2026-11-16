(() => {
  "use strict";
  const token = document.querySelector('meta[name="csrf-token"]')?.content || "";

  async function request(url, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (options.body) {
      headers.set("Content-Type", "application/json");
      headers.set("X-CSRF-Token", token);
    }
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
      headers,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(payload.error || "The request failed.");
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  window.MatcharrAPI = {
    get: (url) => request(url),
    post: (url, payload) => request(url, { method: "POST", body: JSON.stringify(payload) }),
  };
})();

