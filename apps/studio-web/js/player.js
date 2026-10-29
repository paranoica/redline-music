// Two lanes play in lockstep, the original and the result, and A/B only moves their gains,
// so a switch never jumps. Both lanes are levelled to the quieter one.
const FADE = 0.03;

export class Player {
  constructor() {
    this.ctx = new AudioContext();
    this.lanes = {
      original: this.#lane(),
      result: this.#lane(),
      reference: this.#lane(),
    };
    this.buffers = { original: null, result: null, reference: null };
    // the result buffer may cover only the loop: its offset in the track, in seconds
    this.resultStart = 0;
    // a reference is another song: it loops its own loudest stretch, the loop's length
    this.referenceStart = 0;
    this.lufs = { original: 0, result: 0, reference: null };
    this.levels = { original: 0, result: 0, reference: 0 };
    this.side = "result";
    this.loop = { on: true, start: 0, end: 0 };
    this.playing = false;
    this.anchor = { time: 0, position: 0 };
    this.onEnd = () => {};
  }

  #lane() {
    const gain = this.ctx.createGain();
    gain.connect(this.ctx.destination);
    gain.gain.value = 0;
    return { gain, source: null };
  }

  get duration() {
    return this.buffers.original ? this.buffers.original.duration : 0;
  }

  async decode(bytes) {
    return this.ctx.decodeAudioData(bytes.slice(0));
  }

  setOriginal(buffer) {
    this.buffers.original = buffer;
    if (!this.buffers.result) {
      this.buffers.result = buffer;
    }
  }

  // lufs pair: loudness of the result and of the original over the same span
  setResult(buffer, start, resultLufs, originalLufs) {
    const position = this.position();
    this.buffers.result = buffer;
    this.resultStart = start;
    this.lufs.original = originalLufs;
    this.lufs.result = resultLufs;
    this.#level();
    if (this.playing) {
      this.#restart(position);
    } else {
      this.#applyGains();
    }
  }

  setReference(buffer, lufs, start) {
    this.buffers.reference = buffer;
    this.lufs.reference = lufs;
    this.referenceStart = start;
    this.#level();
    if (this.playing) this.#restart(this.position());
  }

  // every side is brought down to the quietest one, so louder never reads as better
  #level() {
    const known = Object.values(this.lufs).filter((v) => v !== null);
    const quiet = Math.min(...known);
    for (const name of Object.keys(this.levels)) {
      this.levels[name] = this.lufs[name] === null ? 0 : quiet - this.lufs[name];
    }
  }

  coversResult(position) {
    const buffer = this.buffers.result;
    return position >= this.resultStart && position < this.resultStart + buffer.duration - 0.01;
  }

  setSide(side) {
    this.side = side;
    this.#applyGains();
  }

  setLoop(loop) {
    const position = this.position();
    this.loop = { ...this.loop, ...loop };
    if (this.playing) {
      this.#restart(this.#wrap(position));
    }
  }

  position() {
    if (!this.playing) {
      return this.anchor.position;
    }
    return this.#wrap(this.anchor.position + this.ctx.currentTime - this.anchor.time);
  }

  #wrap(position) {
    const { on, start, end } = this.loop;
    if (on && end > start && position >= end) {
      return start + ((position - start) % (end - start));
    }
    return Math.min(position, this.duration);
  }

  async play(from = this.position()) {
    await this.ctx.resume();
    this.playing = true;
    this.#restart(from);
  }

  pause() {
    const position = this.position();
    this.#stopSources();
    this.playing = false;
    this.anchor = { time: this.ctx.currentTime, position };
  }

  seek(position) {
    const clamped = Math.max(0, Math.min(position, this.duration));
    if (this.playing) {
      this.#restart(clamped);
    } else {
      this.anchor = { time: this.ctx.currentTime, position: clamped };
    }
  }

  #stopSources() {
    for (const lane of Object.values(this.lanes)) {
      if (lane.source) {
        lane.source.onended = null;
        lane.source.stop(this.ctx.currentTime + FADE);
        lane.source = null;
      }
    }
  }

  #restart(position) {
    const now = this.ctx.currentTime;
    for (const lane of Object.values(this.lanes)) {
      lane.gain.gain.setTargetAtTime(0, now, FADE / 3);
    }
    this.#stopSources();
    const at = now + FADE;
    const { on, start, end } = this.loop;
    for (const name of ["original", "result"]) {
      const buffer = this.buffers[name];
      const offset = name === "result" ? this.resultStart : 0;
      const source = this.ctx.createBufferSource();
      source.buffer = buffer;
      if (on && end > start) {
        source.loop = true;
        source.loopStart = Math.max(0, start - offset);
        source.loopEnd = Math.min(buffer.duration, end - offset);
      }
      source.connect(this.lanes[name].gain);
      source.start(at, Math.max(0, Math.min(position - offset, buffer.duration - 0.001)));
      this.lanes[name].source = source;
    }
    const reference = this.buffers.reference;
    if (reference) {
      const source = this.ctx.createBufferSource();
      source.buffer = reference;
      source.loop = true;
      let offset = position % reference.duration;
      if (on && end > start) {
        const length = Math.min(end - start, reference.duration);
        source.loopStart = this.referenceStart;
        source.loopEnd = Math.min(reference.duration, this.referenceStart + length);
        offset = this.referenceStart + ((position - start) % length + length) % length;
      }
      source.connect(this.lanes.reference.gain);
      source.start(at, Math.min(offset, reference.duration - 0.001));
      this.lanes.reference.source = source;
    }
    this.lanes.original.source.onended = () => {
      if (this.playing && !this.loop.on) {
        this.playing = false;
        this.anchor = { time: this.ctx.currentTime, position: 0 };
        this.onEnd();
      }
    };
    this.anchor = { time: at, position };
    this.#applyGains(at);
  }

  #applyGains(at = this.ctx.currentTime) {
    for (const name of ["original", "result", "reference"]) {
      const db = this.levels[name];
      const target = name === this.side ? 10 ** (db / 20) : 0;
      this.lanes[name].gain.gain.setTargetAtTime(target, at, FADE / 3);
    }
  }
}
