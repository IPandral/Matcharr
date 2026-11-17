const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const readScript = (name) => fs.readFileSync(
  path.join(__dirname, "../../app/static/js", name), "utf8"
);
const api = readScript("api.js");
const swipe = readScript("swipe.js");
const flush = () => new Promise(setImmediate);
const resultsUrl = "/rooms/room/matches?session_id=session";

function client(room, storage = new Map()) {
  const elements = new Map();
  const timers = new Map();
  const redirects = [];
  const requests = [];
  const document = Object.assign(new EventTarget(), {
    hidden: false,
    querySelector: () => null,
    getElementById(id) {
      if (!elements.has(id)) {
        elements.set(id, Object.assign(new EventTarget(), {
          open: false,
          showModal() { this.open = true; },
          close() { this.open = false; },
        }));
      }
      return elements.get(id);
    },
  });
  const element = (id) => document.getElementById(id);
  element("swipe-app").dataset = {
    roomId: "room", sessionId: "session", membershipId: "member", pollInterval: "750",
  };
  let nextTimer = 0;
  const window = Object.assign(new EventTarget(), {
    MatcharrSwipeLogic: {},
    sessionStorage: {
      getItem: (key) => storage.get(key),
      setItem: (key, value) => storage.set(key, value),
    },
    location: { replace: (url) => redirects.push(url) },
    setTimeout(callback, delay) {
      timers.set(++nextTimer, { callback, delay });
      return nextTimer;
    },
    clearTimeout(id) { timers.delete(id); },
  });
  const fetch = async (url) => {
    const action = url.split("/").pop();
    requests.push(action);
    const progress = {
      total: 3, decided: 3, remaining: 0,
      eligible_members: 2, finish_votes: room.finishVotes || 0,
      session_status: room.status,
    };
    return {
      ok: true,
      json: async () => action === "status"
        ? { session: { status: room.status }, progress, matches: [...room.matches] }
        : { movie: null, progress },
    };
  };
  const context = vm.createContext({ window, document, fetch, Headers, AbortController });
  vm.runInContext(api, context);
  vm.runInContext(swipe, context);
  return {
    element, redirects, requests, document, storage,
    async poll() {
      const timer = [...timers].find(([, entry]) => entry.delay === 750);
      assert.ok(timer, "the next status check should be scheduled at the configured interval");
      timers.delete(timer[0]);
      await timer[1].callback();
    },
    continueSwiping() { element("match-continue-button").dispatchEvent(new Event("click")); },
    matchTitle() { return element("match-notification-title").textContent; },
    matchOpen() { return element("match-notification-dialog").open; },
  };
}

test("both devices see subsequent matches and a refresh preserves only acknowledged matches", async () => {
  const room = { status: "active", matches: [] };
  const firstPhone = client(room);
  const secondPhone = client(room);
  await flush();

  room.matches.push({ id: "m1", title: "Arrival" });
  for (const phone of [firstPhone, secondPhone]) {
    await phone.poll();
    assert.equal(phone.matchTitle(), "Arrival");
    phone.continueSwiping();
    await phone.poll();
    assert.equal(phone.matchOpen(), false);
  }

  room.matches.push({ id: "m2", title: "Moon" });
  await firstPhone.poll();
  await secondPhone.poll();
  assert.equal(firstPhone.matchTitle(), "Moon");
  assert.equal(secondPhone.matchTitle(), "Moon");
  const reloaded = client(room, secondPhone.storage);
  await flush();
  assert.equal(reloaded.matchTitle(), "Moon");
  reloaded.continueSwiping();
  assert.equal(reloaded.matchOpen(), false);
});

test("completion opens final voting even with an open match dialog or a backgrounded browser", async () => {
  const room = { status: "active", matches: [{ id: "m1", title: "Arrival" }] };
  const firstPhone = client(room);
  const secondPhone = client(room);
  await flush();
  assert.equal(firstPhone.matchOpen(), true);
  assert.equal(secondPhone.matchOpen(), true);

  secondPhone.document.hidden = true;
  room.status = "completed";
  room.finishVotes = 2;
  await firstPhone.poll();
  await secondPhone.poll();
  assert.deepEqual(firstPhone.redirects, [resultsUrl]);
  assert.deepEqual(secondPhone.redirects, []);
  assert.equal(secondPhone.requests.filter((action) => action === "status").length, 1);

  secondPhone.document.hidden = false;
  secondPhone.document.dispatchEvent(new Event("visibilitychange"));
  await flush();
  assert.deepEqual(secondPhone.redirects, [resultsUrl]);
});
