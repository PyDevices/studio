// A fake Web MIDI keyboard, installed by Playwright before the page loads.
//
// It replaces navigator.requestMIDIAccess with one input and one output
// named like a Donner DMK-25 PRO. Everything above the operating
// system runs unchanged: the page's onmidimessage handler gets MIDIMessage-
// like events whose timeStamp is when the key was "pressed", and they're
// dispatched on the page's event loop like real ones, so a busy page delays
// them exactly as it would delay a real keyboard's.
//
//   window.__fakeMidi.play([[ms_from_now, [0x90, 60, 100]], ...])
//   window.__fakeMidi.unplug() / .plug()
(() => {
  const NAME = "DMK-25 PRO";
  const listeners = new Set();
  const stateListeners = new Set();
  let access = null;

  function makePort(type, id) {
    const port = {
      id, name: NAME, manufacturer: "Donner", version: "1.0", type,
      state: "connected", connection: "closed", onmidimessage: null, onstatechange: null,
      addEventListener(ev, fn) { if (ev === "midimessage") listeners.add(fn); if (ev === "statechange") stateListeners.add(fn); },
      removeEventListener(ev, fn) { listeners.delete(fn); stateListeners.delete(fn); },
      open() { port.connection = "open"; return Promise.resolve(port); },
      close() { port.connection = "closed"; return Promise.resolve(port); },
      send() {}, clear() {},
    };
    return port;
  }

  const input = makePort("input", "fake-dmk25-in");
  const output = makePort("output", "fake-dmk25-out");
  const sent = [];

  function deliver(bytes, stamp) {
    const ev = { data: new Uint8Array(bytes), timeStamp: stamp, type: "midimessage", target: input };
    if (input.state !== "connected") return;
    input.connection = "open";
    if (typeof input.onmidimessage === "function") input.onmidimessage(ev);
    for (const fn of listeners) fn(ev);
  }

  // Timers alone would deliver late: Chrome on Windows runs setTimeout on a
  // 15.6 ms tick, so a note would wait up to 16 ms for no reason a real
  // keyboard has (its timeStamp is when the OS got the bytes). A timer wakes
  // the pump LEAD_MS early, and from there it re-posts itself on a
  // MessageChannel, which isn't tied to the tick, until the event is due.
  // Each turn yields to the page, so a busy page still delays the event
  // exactly as it would a real one, and that delay is still counted.
  const LEAD_MS = 20;
  const pending = [];
  let timer = null, spinning = false;
  const spin = new MessageChannel();
  spin.port1.onmessage = () => { spinning = false; pump(); };
  function pump() {
    while (pending.length && pending[0].stamp <= performance.now()) {
      const e = pending.shift();
      sent.push({ stamp: e.stamp, d: e.bytes, at: performance.now() });
      deliver(e.bytes, e.stamp);
    }
    if (!pending.length) return;
    const wait = pending[0].stamp - performance.now();
    if (wait > LEAD_MS) {
      if (timer === null) timer = setTimeout(() => { timer = null; pump(); }, wait - LEAD_MS);
    } else if (!spinning) {
      spinning = true;
      spin.port2.postMessage(0);
    }
  }

  function makeAccess() {
    const inputs = new Map(), outputs = new Map();
    const a = {
      inputs, outputs, sysexEnabled: false, onstatechange: null,
      addEventListener(ev, fn) { if (ev === "statechange") stateListeners.add(fn); },
      removeEventListener(ev, fn) { stateListeners.delete(fn); },
    };
    const refresh = () => {
      inputs.clear(); outputs.clear();
      if (input.state === "connected") { inputs.set(input.id, input); outputs.set(output.id, output); }
    };
    refresh();
    a._refresh = refresh;
    return a;
  }

  function changeState(state) {
    input.state = output.state = state;
    if (!access) return;
    access._refresh();
    for (const port of [input, output]) {
      const ev = { port, type: "statechange" };
      if (typeof access.onstatechange === "function") access.onstatechange(ev);
      for (const fn of stateListeners) fn(ev);
    }
  }

  navigator.requestMIDIAccess = () => {
    access = access || makeAccess();
    return Promise.resolve(access);
  };

  window.__fakeMidi = {
    // events: [[ms_from_now, [bytes...]], ...]. Each is delivered at its
    // time; its timeStamp is the intended time even if the page is late.
    play(events) {
      const t0 = performance.now();
      for (const [ms, bytes] of events) pending.push({ stamp: t0 + ms, bytes });
      pending.sort((a, b) => a.stamp - b.stamp);
      if (timer !== null) { clearTimeout(timer); timer = null; }
      pump();
      return t0;
    },
    send(bytes) { const s = performance.now(); sent.push({ stamp: s, d: bytes }); deliver(bytes, s); },
    takeSent() { const got = sent.splice(0); return got; },
    unplug() { changeState("disconnected"); },
    plug() { changeState("connected"); },
  };
})();
