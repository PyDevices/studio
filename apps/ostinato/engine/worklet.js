// The AudioWorklet end: plays the engine's blocks and asks for more.
//
// It keeps `target` frames queued. Whenever queued plus already-requested
// frames fall below that, it asks the engine for enough 256-frame blocks to
// make it up. An empty queue once playing has started is an underrun.
//
// The node's own port (to the page) carries stats and, when a test turns it
// on, the tap: a copy of every frame played, with the frame it started at.

const BLOCK = 256;

class OstinatoOut extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = options.processorOptions || {};
    this.target = o.target || 768;
    this.plant = o.plant || "";   // "drop-block": silence one quantum a second, to prove the check
    this.queue = [];        // Int16Array blocks
    this.head = 0;          // read position in queue[0]
    this.queued = 0;        // frames in queue
    this.requested = 0;     // frames asked for, not yet arrived
    this.engine = null;
    this.started = false;
    this.underruns = 0;
    this.underrunFrames = 0;
    this.minQueued = Infinity;
    this.rtSum = 0; this.rtN = 0; this.rtMax = 0;   // request round trip, ms
    this.played = 0;        // queue frames output so far
    this.offset = null;     // context frame - rendered frame, told to the engine
    this.tap = null;        // Float32Array chunk being filled
    this.tapAt = 0;
    this.tapStart = 0;
    this.port.onmessage = (e) => this.onPage(e);
  }

  onPage(e) {
    const m = e.data;
    if (m.type === "engine") {
      this.engine = e.ports[0];
      this.engine.onmessage = (ev) => {
        const pcm = ev.data.pcm;
        if (ev.data.asked !== undefined) {
          const rt = (currentFrame - ev.data.asked) / sampleRate * 1000;
          this.rtSum += rt; this.rtN++; if (rt > this.rtMax) this.rtMax = rt;
        }
        this.queue.push(pcm);
        this.queued += pcm.length;
        this.requested = Math.max(0, this.requested - pcm.length);
      };
      this.ask();
    } else if (m.type === "ring") {
      // SharedArrayBuffer ring: hdr[0] frames written, hdr[1] frames read
      // (both running totals); data is the Int16 ring itself.
      this.hdr = new Int32Array(m.sab, 0, 2);
      this.ring = new Int16Array(m.sab, 8);
    } else if (m.type === "tap") {
      this.tap = m.on ? new Float32Array(sampleRate / 2) : null;
      this.tapAt = 0;
    } else if (m.type === "target") {
      this.target = m.frames;
    } else if (m.type === "stats") {
      this.port.postMessage({ type: "stats", underruns: this.underruns, underrunFrames: this.underrunFrames,
        queued: this.queued, minQueued: this.minQueued, frame: currentFrame, time: currentTime, target: this.target,
        rtAvg: this.rtN ? this.rtSum / this.rtN : null, rtMax: this.rtMax });
      this.minQueued = Infinity; this.rtSum = 0; this.rtN = 0; this.rtMax = 0;
    }
  }

  ask() {
    const short = this.target - this.queued - this.requested;
    if (short > 0 && this.engine) {
      const blocks = Math.ceil(short / BLOCK);
      this.requested += blocks * BLOCK;
      this.engine.postMessage({ need: blocks, asked: currentFrame });
    }
  }

  process(inputs, outputs) {
    if (this.ring) return this.processRing(outputs[0][0]);
    const out = outputs[0][0];
    const frames = out.length;
    let i = 0;
    while (i < frames && this.queue.length) {
      const blk = this.queue[0];
      const n = Math.min(frames - i, blk.length - this.head);
      for (let k = 0; k < n; k++) out[i + k] = blk[this.head + k] / 32768;
      i += n;
      this.head += n;
      this.queued -= n;
      if (this.head >= blk.length) { this.queue.shift(); this.head = 0; }
    }
    if (i < frames) {
      out.fill(0, i);
      if (this.started) { this.underruns++; this.underrunFrames += frames - i; }
    } else {
      this.started = true;
    }
    this.played += i;
    const offset = currentFrame + frames - this.played;
    if (this.engine && this.started && offset !== this.offset) {
      this.offset = offset;
      this.engine.postMessage({ offset });
    }
    if (this.started && this.queued < this.minQueued) this.minQueued = this.queued;
    if (this.plant === "drop-block" && currentFrame % sampleRate < frames) out.fill(0);
    if (this.tap) this.record(out);
    this.ask();
    return true;
  }

  processRing(out) {
    const frames = out.length, hdr = this.hdr, ring = this.ring, size = ring.length;
    const w = Atomics.load(hdr, 0);
    let r = Atomics.load(hdr, 1);
    const n = Math.min(frames, w - r);
    for (let k = 0; k < n; k++) out[k] = ring[(r + k) % size] / 32768;
    r += n;
    Atomics.store(hdr, 1, r);
    Atomics.notify(hdr, 1);
    this.queued = w - r;
    if (n < frames) {
      out.fill(0, n);
      if (this.started) { this.underruns++; this.underrunFrames += frames - n; }
    } else {
      this.started = true;
    }
    if (this.started && this.queued < this.minQueued) this.minQueued = this.queued;
    if (this.plant === "drop-block" && currentFrame % sampleRate < frames) out.fill(0);
    if (this.tap) this.record(out);
    return true;
  }

  record(out) {
    if (this.tapAt === 0) this.tapStart = currentFrame;
    this.tap.set(out, this.tapAt);
    this.tapAt += out.length;
    if (this.tapAt + out.length > this.tap.length) {
      const chunk = this.tap.slice(0, this.tapAt);
      this.port.postMessage({ type: "tap", start: this.tapStart, pcm: chunk }, [chunk.buffer]);
      this.tapAt = 0;
    }
  }
}

registerProcessor("ostinato-out", OstinatoOut);
