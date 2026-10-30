// The beat page's side of its worklet: every command answered with a result, reports of
// the position and meters to subscribers, and the node made as the studio's host does.

import type { PortLike } from "../engine/client.ts";
import type { BeatCommand, BeatPosition, BeatResult, FromBeat, Zone } from "./protocol.ts";

export type { BeatPosition, BeatResult, Zone } from "./protocol.ts";

// commands after which the reports from before tell of another spot
const MOVES = new Set<BeatCommand["type"]>(["load", "update", "rack", "master", "seek", "play", "loop"]);

export class BeatClient {
  onEnded: () => void = () => {};
  onFatal: (message: string) => void = () => {};
  // of the score loaded last, as the worklet says after each command
  length = 0;
  latency = 0;
  #dead: string | null = null;
  readonly #port: PortLike;
  readonly #pending = new Map<number, (result: BeatResult) => void>();
  readonly #listeners = new Set<(position: BeatPosition) => void>();
  #id = 0;
  #moved = 0;

  constructor(port: PortLike) {
    this.#port = port;
    port.onmessage = (event) => this.#receive(event.data as FromBeat);
  }

  get dead(): boolean {
    return this.#dead !== null;
  }

  load(score: string): Promise<BeatResult> {
    return this.#call({ type: "load", score });
  }

  update(score: string): Promise<BeatResult> {
    return this.#call({ type: "update", score });
  }

  setRack(sound: string, rack: string): Promise<BeatResult> {
    return this.#call({ type: "rack", sound, rack });
  }

  setMasterRack(rack: string): Promise<BeatResult> {
    return this.#call({ type: "master", rack });
  }

  // the samples are handed over to the worklet and are empty here afterwards
  loadZone(zone: Zone): Promise<BeatResult> {
    return this.#call({ type: "zone", zone }, [zone.samples.buffer]);
  }

  rebuildSamplers(): Promise<BeatResult> {
    return this.#call({ type: "rebuild" });
  }

  seek(frame: number): Promise<BeatResult> {
    return this.#call({ type: "seek", frame });
  }

  play(): Promise<BeatResult> {
    return this.#call({ type: "play" });
  }

  stop(): Promise<BeatResult> {
    return this.#call({ type: "stop" });
  }

  loop(range: { start: number; end: number } | null): Promise<BeatResult> {
    return this.#call({ type: "loop", range });
  }

  repeat(on: boolean): Promise<BeatResult> {
    return this.#call({ type: "repeat", on });
  }

  // position and meters a few times a second while playing; returns the unsubscribe
  subscribe(listener: (position: BeatPosition) => void): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  // the worklet is stopped for good: told once, and every waiting call gets the reason
  fail(message: string): void {
    if (this.#dead !== null) return;
    this.#dead = message;
    for (const resolve of this.#pending.values()) resolve({ ok: false, errors: [message] });
    this.#pending.clear();
    this.onFatal(message);
  }

  #call(command: BeatCommand, transfer?: Transferable[]): Promise<BeatResult> {
    if (this.#dead !== null) return Promise.resolve({ ok: false, errors: [this.#dead] });
    const id = ++this.#id;
    if (MOVES.has(command.type)) this.#moved = id;
    return new Promise((resolve) => {
      this.#pending.set(id, resolve);
      this.#port.postMessage({ ...command, id }, transfer);
    });
  }

  #receive(m: FromBeat): void {
    switch (m.type) {
      case "result": {
        this.length = m.length;
        this.latency = m.latency;
        const resolve = this.#pending.get(m.id);
        this.#pending.delete(m.id);
        return resolve?.(m.result);
      }
      case "position":
        if (m.after < this.#moved) return;
        for (const listener of this.#listeners) listener(m);
        return;
      case "ended":
        return this.onEnded();
      case "fatal":
        return this.fail(m.message);
      case "ready":
      case "error":
        return;
    }
  }
}

export interface BeatHost {
  ctx: AudioContext;
  node: AudioWorkletNode;
  client: BeatClient;
}

// the context runs at the rate the scores are rendered at, so the page hears the render
export async function startBeat(rate = 48000): Promise<BeatHost> {
  const ctx = new AudioContext({ sampleRate: rate, latencyHint: "interactive" });
  await ctx.audioWorklet.addModule(new URL("beat-worklet.js", document.baseURI));
  const node = new AudioWorkletNode(ctx, "redline-beat", {
    numberOfInputs: 0,
    numberOfOutputs: 1,
    outputChannelCount: [2],
  });
  node.connect(ctx.destination);
  const response = await fetch(new URL("redline_dsp.wasm", document.baseURI));
  if (!response.ok) throw new Error(`the beat engine's wasm did not load (${response.status})`);
  const bytes = await response.arrayBuffer();
  let client: BeatClient | null = null;
  const ready = new Promise<void>((resolve, reject) => {
    node.port.onmessage = (event: MessageEvent<FromBeat>) => {
      if (event.data.type === "ready") resolve();
      else if (event.data.type === "error") reject(new Error(event.data.message));
    };
    // the processor threw past its own guard: the browser stops calling it
    node.onprocessorerror = (event: Event) => {
      const message = event instanceof ErrorEvent && event.message ? event.message : "the beat processor threw";
      if (client) client.fail(message);
      else reject(new Error(message));
    };
  });
  node.port.postMessage({ type: "wasm", bytes }, [bytes]);
  await ready;
  let handler: PortLike["onmessage"] = null;
  const port: PortLike = {
    postMessage: (m, transfer) => node.port.postMessage(m, transfer ?? []),
    get onmessage() {
      return handler;
    },
    set onmessage(f: PortLike["onmessage"]) {
      handler = f;
      node.port.onmessage = f ? (event: MessageEvent) => f({ data: event.data }) : null;
    },
  };
  client = new BeatClient(port);
  return { ctx, node, client };
}
