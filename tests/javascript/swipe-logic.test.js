"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  decisionForKey,
  gestureDecision,
  InputGate,
  createIdempotencyKey,
} = require("../../app/static/js/swipe-logic.js");

test("left arrow skips and held keys are ignored", () => {
  assert.equal(decisionForKey("ArrowLeft"), "skip");
  assert.equal(decisionForKey("ArrowLeft", true), null);
});

test("right arrow likes", () => {
  assert.equal(decisionForKey("ArrowRight"), "like");
});

test("distance threshold accepts horizontal swipes", () => {
  assert.equal(gestureDecision(101, 500, 100, 0.5), "like");
  assert.equal(gestureDecision(-101, 500, 100, 0.5), "skip");
});

test("velocity accepts a fast gesture and cancellation returns null", () => {
  assert.equal(gestureDecision(50, 50, 100, 0.5), "like");
  assert.equal(gestureDecision(20, 200, 100, 0.5), null);
});

test("input gate rejects duplicate input until released", () => {
  const gate = new InputGate();
  assert.equal(gate.acquire(), true);
  assert.equal(gate.acquire(), false);
  gate.release();
  assert.equal(gate.acquire(), true);
});

test("idempotency UUID works when randomUUID is unavailable on LAN phones", () => {
  const fakeCrypto = {
    getRandomValues(bytes) {
      for (let index = 0; index < bytes.length; index += 1) bytes[index] = index;
      return bytes;
    },
  };
  assert.match(
    createIdempotencyKey(fakeCrypto),
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
  );
});
