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

  function get(url, signal) {
    return request(url, { cache: "no-store", signal });
  }

  function watch(url, interval, onUpdate, onError) {
    let timer;
    let pending = false;
    let stopped = false;

    async function refresh() {
      window.clearTimeout(timer);
      if (stopped || pending || document.hidden) return;
      pending = true;
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 8000);
      try {
        const payload = await get(url, controller.signal);
        if (!stopped) onUpdate(payload);
      } catch (error) {
        if (!stopped) onError?.(error);
      } finally {
        window.clearTimeout(timeout);
        pending = false;
        if (!stopped && !document.hidden) timer = window.setTimeout(refresh, interval);
      }
    }

    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("pageshow", refresh);
    window.addEventListener("online", refresh);
    refresh();
    return {
      refresh,
      stop() {
        stopped = true;
        window.clearTimeout(timer);
        document.removeEventListener("visibilitychange", refresh);
        window.removeEventListener("pageshow", refresh);
        window.removeEventListener("online", refresh);
      },
    };
  }

  window.MatcharrAPI = {
    get,
    watch,
    post: (url, payload) => request(url, { method: "POST", body: JSON.stringify(payload) }),
  };
})();

