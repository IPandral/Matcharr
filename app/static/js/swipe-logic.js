((root, factory) => {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.MatcharrSwipeLogic = api;
})(typeof globalThis !== "undefined" ? globalThis : this, () => {
  "use strict";

  function decisionForKey(key, repeat = false) {
    if (repeat) return null;
    if (key === "ArrowLeft") return "skip";
    if (key === "ArrowRight") return "like";
    if (key === "Enter" || key === " ") return "details";
    if (key === "Escape") return "close";
    return null;
  }

  function gestureDecision(dx, elapsedMs, distanceThreshold, velocityThreshold) {
    const velocity = Math.abs(dx) / Math.max(elapsedMs, 1);
    const accepted =
      Math.abs(dx) >= distanceThreshold ||
      (Math.abs(dx) > 30 && velocity >= velocityThreshold);
    if (!accepted) return null;
    return dx > 0 ? "like" : "skip";
  }

  class InputGate {
    constructor() { this.locked = false; }
    acquire() {
      if (this.locked) return false;
      this.locked = true;
      return true;
    }
    release() { this.locked = false; }
  }

  function createIdempotencyKey(cryptoSource = globalThis.crypto) {
    if (cryptoSource && typeof cryptoSource.randomUUID === "function") {
      return cryptoSource.randomUUID();
    }
    const bytes = new Uint8Array(16);
    if (cryptoSource && typeof cryptoSource.getRandomValues === "function") {
      cryptoSource.getRandomValues(bytes);
    } else {
      for (let index = 0; index < bytes.length; index += 1) {
        bytes[index] = Math.floor(Math.random() * 256);
      }
    }
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }

  return { decisionForKey, gestureDecision, InputGate, createIdempotencyKey };
});
