// The AudioWorklet end of Python workers' audio (audiodev.worklet_audio).
//
// One or more workers each send a stream of 256-frame int16 blocks (stereo
// interleaved when channels is 2). The worklet sums the streams, applies the
// master gain and a peak limiter, and plays the result. Several workers can
// share the rendering of one app this way, each on its own core. Per stream,
// over its MessagePort:
//
//   worklet -> worker  {need: blocks, asked: frame}   send that many blocks
//   worker -> worklet  {pcm: Int16Array, asked}       (transferred, interleaved)
//   worklet -> worker  {offset}                       rendered frame k plays at
//                                                     context frame k + offset
//   worklet -> worker  {underrun: count}              underruns since the last one
//
// The queue adapts: it starts at `startFrames` and steps up 512 frames (to
// at most `maxFrames`) on an underrun, once per quarter second however many
// quanta run short, then holds for two seconds. After that it comes back
// down quickly: whenever every stream's low point over two seconds leaves
// 512 frames or more to spare above 384, the target drops by what was spare,
// and the extra audio queued plays out within a few milliseconds. Near the
// floor it steps down 256 frames at a time towards `minFrames` while the low
// point stays above 384 for two seconds. The browser's audio thread asks for
// audio about every 10 ms, so a queue much under 768 frames can't keep up.
//
// Two things keep the first seconds clean. The windows above start when a
// stream first plays, not when the context was created (the context runs for
// seconds while the workers load, and a window anchored there would see
// "plenty to spare" on the first block and drop the target to the floor).
// And the queue never goes below one audio callback's worth of frames: the
// browser's audio thread runs all of a callback's quanta back to back, and
// answers to the requests they make arrive only after it finishes, so each
// callback must be served from what is already queued. The worklet measures
// that size from the gaps between its calls, from the first block, so a sink
// that calls every 25 ms (about 1200 frames) is served without first
// learning it from underruns.
//
// The node's own port (to the page) takes {type: "worker"} with a port per
// stream, {type: "master", gain}, {type: "stats"}, and {type: "tap", on}
// (a test copy of every frame played).

const BLOCK = 256;
const QUANTUM = 128;   // what the browser asks process() for

class Stream {
  constructor(port, owner, index) {
    this.port = port;
    this.index = index;
    this.queue = [];
    this.head = 0;
    this.queued = 0;
    this.requested = 0;
    this.played = 0;
    this.offset = null;
    this.started = false;
    this.underruns = 0;
    this.renderMs = 0;
    this.paused = false;
    port.onmessage = (ev) => {
      if (ev.data.pause !== undefined) {
        this.paused = !!ev.data.pause;
        if (!this.paused) { this.queue = []; this.head = 0; this.queued = 0; this.requested = 0; this.started = false; }
        return;
      }
      const pcm = ev.data.pcm;
      if (!pcm) return;
      const frames = pcm.length / owner.channels;
      this.queue.push(pcm);
      this.queued += frames;
      this.requested = Math.max(0, this.requested - frames);
    };
  }
}

class PythonWorkerOutput extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const o = options.processorOptions || {};
    this.channels = o.channels || 1;
    this.minFrames = o.minFrames || 768;
    this.maxFrames = o.maxFrames || 4096;
    this.target = o.startFrames || 1536;
    this.streams = [];
    this.gain = 1.0;
    this.env = 0;             // limiter's peak follower
    this.ceiling = 0.97;
    this.release = Math.exp(-1 / (0.05 * sampleRate));
    this.underruns = 0;
    this.windowLow = Infinity;
    this.windowStart = 0;
    this.fastLow = Infinity;
    this.fastStart = 0;
    this.holdUntil = 0;
    this.raisedAt = -Infinity;
    this.lastShort = -Infinity;
    this.hold = 2 * sampleRate;
    this.low = Infinity;
    this.peak = 0;
    this.anchored = false;       // the adaptation windows start when a stream first plays
    this.playFrom = 0;           // the context frame that happened at
    this.shortAt = [];           // when the first underruns came: [frames after playFrom, target]
    this.lastCall = 0;           // Date.now() at the previous process()
    this.callback = 0;           // frames in the audio callback now running
    this.burst = 0;              // the largest callback in the window now ending
    this.lastBurst = 0;          // ... and in the one before
    this.burstStart = 0;
    this.tap = null;
    this.tapAt = 0;
    this.tapStart = 0;
    this.port.onmessage = (e) => this.onPage(e);
  }

  onPage(e) {
    const m = e.data;
    if (m.type === "worker") {
      const s = new Stream(e.ports[0], this, this.streams.length);
      this.streams.push(s);
      this.ask(s);
    } else if (m.type === "master") {
      this.gain = m.gain;
    } else if (m.type === "stats") {
      this.port.postMessage({ type: "stats", underruns: this.underruns, target: this.target, floor: this.floor(),
        burst: Math.max(this.burst, this.lastBurst), shortAt: this.shortAt,
        low: this.low, peak: this.peak, frame: currentFrame,
        streams: this.streams.map((s) => ({ queued: s.queued, underruns: s.underruns, offset: s.offset })) });
      this.low = Infinity;
      this.peak = 0;
    } else if (m.type === "tap") {
      this.tap = m.on ? new Float32Array(Math.floor(sampleRate / 2) * this.channels) : null;
      this.tapAt = 0;
    }
  }

  ask(s) {
    if (s.paused) return;
    const short = this.target - s.queued - s.requested;
    if (short > 0) {
      const blocks = Math.ceil(short / BLOCK);
      s.requested += blocks * BLOCK;
      s.port.postMessage({ need: blocks, asked: currentFrame });
    }
  }

  // The audio thread calls process() for each quantum of a callback back to
  // back, then waits for the device: a gap of two milliseconds or more
  // between calls starts a new callback.
  measure(frames) {
    const t = Date.now();
    if (t - this.lastCall >= 2) {
      if (this.callback > this.burst) this.burst = this.callback;
      this.callback = 0;
    }
    this.lastCall = t;
    this.callback += frames;
    if (currentFrame - this.burstStart >= 2 * sampleRate) {
      this.lastBurst = this.burst;
      this.burst = 0;
      this.burstStart = currentFrame;
    }
  }

  // The queue's floor: `minFrames`, or one callback and a quantum if that is more.
  floor() {
    const burst = Math.max(this.burst, this.lastBurst, this.callback);
    return Math.min(this.maxFrames, Math.max(this.minFrames, burst + QUANTUM));
  }

  adapt(underran) {
    const now = currentFrame;
    if (!this.anchored) {
      this.anchored = true;
      this.playFrom = now;
      this.windowStart = this.fastStart = now;
      this.holdUntil = now + this.hold;
    }
    const floor = this.floor();
    if (this.target < floor) this.target = floor;
    if (underran) {
      // Short quanta less than a second apart are one stall. After a stall,
      // hold two seconds; when a new one comes within ten seconds of the
      // last, the machine is struggling: hold ten.
      const quiet = now - this.lastShort;
      if (quiet > sampleRate) this.hold = (quiet < 10 * sampleRate ? 10 : 2) * sampleRate;
      this.lastShort = now;
      // Within a stall, raise at most every quarter second: a stall of a few
      // milliseconds would otherwise push the target straight to the maximum.
      if (now - this.raisedAt >= sampleRate / 4) {
        this.target = Math.min(this.maxFrames, this.target + 512);
        this.raisedAt = now;
      }
      this.holdUntil = now + this.hold;
      this.windowStart = this.fastStart = now;
      this.windowLow = this.fastLow = Infinity;
      return;
    }
    if (now - this.fastStart >= 2 * sampleRate) {
      // Plenty to spare: drop by what was spare, all at once.
      const spare = Math.floor((this.fastLow - 384) / BLOCK) * BLOCK;
      if (now >= this.holdUntil && spare >= 2 * BLOCK && this.target > floor) {
        this.target = Math.max(floor, this.target - spare);
        this.windowStart = now;
        this.windowLow = Infinity;
      }
      this.fastStart = now;
      this.fastLow = Infinity;
    }
    if (now - this.windowStart >= 2 * sampleRate) {
      if (now >= this.holdUntil && this.windowLow > 384 && this.target > floor) {
        this.target = Math.max(floor, this.target - BLOCK);
      }
      this.windowStart = now;
      this.windowLow = Infinity;
    }
  }

  // Add one stream's next `frames` frames into `out`; true if it ran short.
  mix(s, out, frames, first) {
    const ch = this.channels;
    if (s.paused) {
      if (first) for (let c = 0; c < out.length; c++) out[c].fill(0);
      return false;
    }
    let i = 0;
    while (i < frames && s.queue.length) {
      const blk = s.queue[0];
      const n = Math.min(frames - i, (blk.length - s.head) / ch);
      for (let c = 0; c < ch; c++) {
        const o = out[c];
        if (first) for (let k = 0; k < n; k++) o[i + k] = blk[s.head + k * ch + c] / 32768;
        else for (let k = 0; k < n; k++) o[i + k] += blk[s.head + k * ch + c] / 32768;
      }
      i += n;
      s.head += n * ch;
      s.queued -= n;
      if (s.head >= blk.length) { s.queue.shift(); s.head = 0; }
    }
    if (first && i < frames) for (let c = 0; c < out.length; c++) out[c].fill(0, i);
    s.played += i;
    let short = false;
    if (i < frames) {
      if (s.started) {
        short = true;
        s.underruns++;
        s.port.postMessage({ underrun: 1 });
      }
    } else {
      s.started = true;
    }
    if (s.started) {
      const offset = currentFrame + frames - s.played;
      if (offset !== s.offset) {
        s.offset = offset;
        s.port.postMessage({ offset });
      }
    }
    return short;
  }

  process(inputs, outputs) {
    const out = outputs[0];
    const frames = out[0].length;
    this.measure(frames);
    let underran = false;
    if (!this.streams.length) {
      for (let c = 0; c < out.length; c++) out[c].fill(0);
    }
    for (let j = 0; j < this.streams.length; j++) {
      if (this.mix(this.streams[j], out, frames, j === 0)) underran = true;
    }
    // Master gain and a peak limiter: fast attack, 50 ms release.
    const ch = this.channels;
    for (let k = 0; k < frames; k++) {
      let p = 0;
      for (let c = 0; c < ch; c++) {
        out[c][k] *= this.gain;
        const a = Math.abs(out[c][k]);
        if (a > p) p = a;
      }
      this.env = p > this.env ? p : this.env * this.release;
      if (p > this.peak) this.peak = p;
      if (this.env > this.ceiling) {
        const g = this.ceiling / this.env;
        for (let c = 0; c < ch; c++) out[c][k] *= g;
      }
    }
    if (underran) {
      this.underruns++;
      if (this.shortAt.length < 16) this.shortAt.push([currentFrame - this.playFrom, this.target]);
    }
    for (const s of this.streams) {
      if (s.started && s.queued < this.windowLow) this.windowLow = s.queued;
      if (s.started && s.queued < this.fastLow) this.fastLow = s.queued;
      if (s.started && s.queued < this.low) this.low = s.queued;
    }
    if (this.streams.some((s) => s.started)) this.adapt(underran);
    if (this.tap) this.record(out, frames);
    for (const s of this.streams) this.ask(s);
    return true;
  }

  record(out, frames) {
    if (this.tapAt === 0) this.tapStart = currentFrame;
    const ch = this.channels;
    for (let k = 0; k < frames; k++) {
      for (let c = 0; c < ch; c++) this.tap[(this.tapAt + k) * ch + c] = out[c][k];
    }
    this.tapAt += frames;
    if ((this.tapAt + frames) * ch > this.tap.length) {
      const chunk = this.tap.slice(0, this.tapAt * ch);
      this.port.postMessage({ type: "tap", start: this.tapStart, channels: ch, pcm: chunk }, [chunk.buffer]);
      this.tapAt = 0;
    }
  }
}

registerProcessor("python-worker-output", PythonWorkerOutput);
