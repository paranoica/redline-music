// The track as two envelopes, the original under the result, with the loop as a red bracket.
// Drag a bracket edge to resize the loop, drag inside it to move it, click elsewhere to seek.
const EDGE_PX = 8;
const MIN_LOOP = 2;

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// peak and RMS per column, so a dense master and a sparse original read differently
function envelope(buffer, columns, from = 0, to = buffer.duration) {
  const rate = buffer.sampleRate;
  const channels = [...Array(buffer.numberOfChannels).keys()].map((c) => buffer.getChannelData(c));
  const first = Math.floor(from * rate);
  const last = Math.min(Math.floor(to * rate), channels[0].length);
  const step = Math.max(1, Math.floor((last - first) / columns));
  const peaks = new Float32Array(columns);
  const rms = new Float32Array(columns);
  for (let col = 0; col < columns; col++) {
    let peak = 0;
    let sum = 0;
    let count = 0;
    const end = Math.min(first + (col + 1) * step, last);
    for (let i = first + col * step; i < end; i += 4) {
      let v = 0;
      for (const data of channels) {
        v += data[i];
      }
      v /= channels.length;
      const a = Math.abs(v);
      if (a > peak) {
        peak = a;
      }
      sum += v * v;
      count++;
    }
    peaks[col] = peak;
    rms[col] = count ? Math.sqrt(sum / count) : 0;
  }
  return { peaks, rms };
}

export class Waveform {
  constructor(canvas, onSeek, onLoop) {
    this.canvas = canvas;
    this.onSeek = onSeek;
    this.onLoop = onLoop;
    this.duration = 0;
    this.original = null;
    this.result = null;
    this.loop = { start: 0, end: 0 };
    this.position = 0;
    this.drag = null;
    new ResizeObserver(() => this.#resize()).observe(canvas);
    canvas.addEventListener("pointerdown", (e) => this.#down(e));
    canvas.addEventListener("pointermove", (e) => this.#move(e));
    canvas.addEventListener("pointerup", (e) => this.#up(e));
    canvas.addEventListener("pointerleave", () => delete canvas.dataset.hover);
  }

  #resize() {
    const ratio = window.devicePixelRatio || 1;
    const { width, height } = this.canvas.getBoundingClientRect();
    this.canvas.width = Math.round(width * ratio);
    this.canvas.height = Math.round(height * ratio);
    this.#rebuild();
  }

  setOriginal(buffer) {
    this.duration = buffer.duration;
    this.originalBuffer = buffer;
    this.#rebuild();
  }

  // a result covering only the loop is drawn only there
  setResult(buffer, start) {
    this.resultBuffer = buffer;
    this.resultStart = start;
    this.#rebuild();
  }

  setLoop(loop) {
    this.loop = loop;
    this.draw();
  }

  setPosition(position) {
    this.position = position;
    this.draw();
  }

  #rebuild() {
    const columns = Math.max(1, Math.floor(this.canvas.width / 2));
    if (this.originalBuffer) {
      this.original = envelope(this.originalBuffer, columns);
    }
    if (this.resultBuffer && this.duration) {
      const span = this.resultBuffer.duration;
      const cols = Math.max(1, Math.round((columns * span) / this.duration));
      this.result = { start: this.resultStart, span, ...envelope(this.resultBuffer, cols) };
    }
    this.draw();
  }

  #x(seconds) {
    return (seconds / (this.duration || 1)) * this.canvas.width;
  }

  #seconds(clientX) {
    const box = this.canvas.getBoundingClientRect();
    return Math.max(0, Math.min(1, (clientX - box.left) / box.width)) * this.duration;
  }

  draw() {
    const ctx = this.canvas.getContext("2d");
    const { width, height } = this.canvas;
    const mid = height / 2;
    ctx.clearRect(0, 0, width, height);
    if (!this.original) {
      return;
    }
    const ratio = window.devicePixelRatio || 1;
    const drawEnvelope = (env, x0, colWidth, peakColor, rmsColor) => {
      for (let i = 0; i < env.peaks.length; i++) {
        const x = x0 + i * colWidth;
        const p = env.peaks[i] * mid * 0.95;
        const r = env.rms[i] * mid * 0.95 * 1.4;
        ctx.fillStyle = peakColor;
        ctx.fillRect(x, mid - p, Math.max(1, colWidth - ratio), p * 2);
        ctx.fillStyle = rmsColor;
        ctx.fillRect(x, mid - r, Math.max(1, colWidth - ratio), r * 2);
      }
    };
    drawEnvelope(this.original, 0, width / this.original.peaks.length, "#2f3237", "#4a4e55");
    if (this.result) {
      const x0 = this.#x(this.result.start);
      const w = this.#x(this.result.span) / this.result.peaks.length;
      drawEnvelope(this.result, x0, w, "#ece6dc3d", "#ece6dccc");
    }
    const red = css("--red") || "#ff4b3a";
    const { start, end } = this.loop;
    if (end > start) {
      const a = this.#x(start);
      const b = this.#x(end);
      ctx.fillStyle = "#ff4b3a14";
      ctx.fillRect(a, 0, b - a, height);
      ctx.strokeStyle = red;
      ctx.lineWidth = 2 * ratio;
      const arm = 10 * ratio;
      ctx.beginPath();
      ctx.moveTo(a + arm, 1);
      ctx.lineTo(a + 1, 1);
      ctx.lineTo(a + 1, height - 1);
      ctx.lineTo(a + arm, height - 1);
      ctx.moveTo(b - arm, 1);
      ctx.lineTo(b - 1, 1);
      ctx.lineTo(b - 1, height - 1);
      ctx.lineTo(b - arm, height - 1);
      ctx.stroke();
    }
    const x = this.#x(this.position);
    ctx.fillStyle = red;
    ctx.fillRect(x - ratio / 2, 0, ratio * 1.5, height);
  }

  #hit(clientX) {
    const box = this.canvas.getBoundingClientRect();
    const px = clientX - box.left;
    const a = (this.loop.start / this.duration) * box.width;
    const b = (this.loop.end / this.duration) * box.width;
    if (Math.abs(px - a) <= EDGE_PX) return "start";
    if (Math.abs(px - b) <= EDGE_PX) return "end";
    if (px > a && px < b) return "inside";
    return null;
  }

  #down(event) {
    if (!this.duration) return;
    const part = this.#hit(event.clientX);
    this.canvas.setPointerCapture(event.pointerId);
    this.drag = {
      part,
      from: this.#seconds(event.clientX),
      loop: { ...this.loop },
      moved: false,
    };
  }

  #move(event) {
    if (!this.duration) return;
    if (!this.drag) {
      const part = this.#hit(event.clientX);
      this.canvas.dataset.hover = part === "inside" ? "inside" : part ? "edge" : "";
      return;
    }
    const now = this.#seconds(event.clientX);
    const shift = now - this.drag.from;
    if (Math.abs(shift) > 0.05) this.drag.moved = true;
    const { start, end } = this.drag.loop;
    let next = this.loop;
    if (this.drag.part === "start") {
      next = { start: Math.min(Math.max(0, start + shift), end - MIN_LOOP), end };
    } else if (this.drag.part === "end") {
      next = { start, end: Math.max(Math.min(this.duration, end + shift), start + MIN_LOOP) };
    } else if (this.drag.part === "inside") {
      const len = end - start;
      const s = Math.min(Math.max(0, start + shift), this.duration - len);
      next = { start: s, end: s + len };
    }
    this.setLoop(next);
  }

  #up(event) {
    if (!this.drag) return;
    const { part, moved } = this.drag;
    this.drag = null;
    if (moved && part) {
      this.onLoop(this.loop);
    } else {
      this.onSeek(this.#seconds(event.clientX));
    }
  }
}

// the loudest stretch of the given length, a good default loop (the chorus, most likely)
export function loudestSpan(buffer, length) {
  const data = buffer.getChannelData(0);
  const rate = buffer.sampleRate;
  const block = Math.floor(rate / 2);
  const energy = [];
  for (let i = 0; i + block <= data.length; i += block) {
    let sum = 0;
    for (let j = i; j < i + block; j += 8) sum += data[j] * data[j];
    energy.push(sum);
  }
  const span = Math.min(energy.length, Math.round(length * 2));
  let best = 0;
  let total = energy.slice(0, span).reduce((a, b) => a + b, 0);
  let bestTotal = total;
  for (let i = span; i < energy.length; i++) {
    total += energy[i] - energy[i - span];
    if (total > bestTotal) {
      bestTotal = total;
      best = i - span + 1;
    }
  }
  const start = best / 2;
  return { start, end: Math.min(buffer.duration, start + length) };
}
