// WAV bytes to interleaved stereo float: 32-bit float and 16-bit PCM, mono spread to both sides.

export interface Audio {
  rate: number;
  frames: number;
  data: Float32Array;
}

const PCM = 1;
const FLOAT = 3;
const EXTENSIBLE = 0xfffe;

function tag(view: DataView, at: number): string {
  return String.fromCharCode(view.getUint8(at), view.getUint8(at + 1), view.getUint8(at + 2), view.getUint8(at + 3));
}

export function parseWav(bytes: ArrayBuffer): Audio {
  const view = new DataView(bytes);
  if (bytes.byteLength < 12 || tag(view, 0) !== "RIFF" || tag(view, 8) !== "WAVE") {
    throw new Error("not a WAV file");
  }
  let format = 0;
  let channels = 0;
  let rate = 0;
  let bits = 0;
  let at = 12;
  while (at + 8 <= bytes.byteLength) {
    const id = tag(view, at);
    const size = view.getUint32(at + 4, true);
    const body = at + 8;
    if (id === "fmt ") {
      format = view.getUint16(body, true);
      channels = view.getUint16(body + 2, true);
      rate = view.getUint32(body + 4, true);
      bits = view.getUint16(body + 14, true);
      if (format === EXTENSIBLE && size >= 26) format = view.getUint16(body + 24, true);
    } else if (id === "data") {
      if (!channels) throw new Error("a WAV file without a format before its data");
      const kind = format === FLOAT && bits === 32 ? "float" : format === PCM && bits === 16 ? "pcm16" : null;
      if (kind === null) throw new Error(`a WAV of format ${format}, ${bits}-bit, is not read here`);
      const width = bits / 8;
      const frames = Math.floor(Math.min(size, bytes.byteLength - body) / (width * channels));
      const data = new Float32Array(2 * frames);
      for (let n = 0; n < frames; n++) {
        for (let c = 0; c < 2; c++) {
          const offset = body + (n * channels + Math.min(c, channels - 1)) * width;
          data[2 * n + c] = kind === "float" ? view.getFloat32(offset, true) : view.getInt16(offset, true) / 32768;
        }
      }
      return { rate, frames, data };
    }
    at = body + size + (size % 2);
  }
  throw new Error("a WAV file without data");
}
