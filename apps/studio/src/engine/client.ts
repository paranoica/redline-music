// The page's side of the engine: sends racks as whole JSON only when their structure
// changes, and moved numbers as sets in place.

import { planRack, type Catalogue } from "../core/diff.ts";
import { rackJson } from "../core/racks.ts";
import { TRACKS, type Lane, type RackSlot, type Racks, type Track } from "../types.ts";
import type { FromEngine, Levels, Source, ToEngine } from "./protocol.ts";

export interface PortLike {
  postMessage(message: unknown, transfer?: Transferable[]): void;
  onmessage: ((event: { data: unknown }) => void) | null;
}

interface Sent {
  version: number;
  slots: RackSlot[];
  lanes: Lane[];
}

export class EngineClient {
  onLevels: (levels: Levels) => void = () => {};
  onRack: (track: Track, latency: number) => void = () => {};
  onError: (error: { track?: Track; message: string }) => void = () => {};
  onEnded: () => void = () => {};
  onFatal: (message: string) => void = () => {};
  #dead = false;
  readonly latency: Record<Track, number> = { vocal: 0, beat: 0, master: 0 };
  readonly #port: PortLike;
  readonly #catalogue: Catalogue;
  readonly #sent: Record<Track, Sent | null> = { vocal: null, beat: null, master: null };
  readonly #versions: Record<Track, number> = { vocal: 0, beat: 0, master: 0 };

  constructor(port: PortLike, catalogue: Catalogue) {
    this.#port = port;
    this.#catalogue = catalogue;
    port.onmessage = (event) => this.#receive(event.data as FromEngine);
  }

  get dead(): boolean {
    return this.#dead;
  }

  #send(message: ToEngine, transfer?: Transferable[]): void {
    this.#port.postMessage(message, transfer);
  }

  #receive(m: FromEngine): void {
    switch (m.type) {
      case "levels":
        return this.onLevels(m);
      case "rack":
        if (this.#sent[m.track]?.version === m.version) {
          this.latency[m.track] = m.latency;
          this.onRack(m.track, m.latency);
        }
        return;
      case "error":
        if (m.fatal) return this.fail(m.message);
        // the engine kept the rack before; the next change sends this track whole again
        if (m.track && this.#sent[m.track]?.version === m.version) this.#sent[m.track] = null;
        return this.onError(m.track ? { track: m.track, message: m.message } : { message: m.message });
      case "ended":
        return this.onEnded();
      case "ready":
        return;
    }
  }

  // the engine is stopped for good: told once, whether it said so itself or the browser did
  fail(message: string): void {
    if (this.#dead) return;
    this.#dead = true;
    this.onFatal(message);
  }

  // the arrays are handed over to the worklet and are empty here afterwards
  loadStems(frames: number, vocal: Float32Array, beat: Float32Array, original: Float32Array): void {
    this.#send({ type: "stems", frames, vocal, beat, original }, [vocal.buffer, beat.buffer, original.buffer]);
  }

  setSource(name: "reference" | "render", data: Float32Array | null, start = 0, length = 0): void {
    this.#send({ type: "source", name, data, start, length }, data ? [data.buffer] : []);
  }

  setRacks(racks: Racks, lanes: Lane[]): void {
    for (const track of TRACKS) {
      const slots = racks[track];
      const trackLanes = track === "vocal" ? lanes : [];
      const sent = this.#sent[track];
      const plan = sent ? planRack(sent.slots, slots, this.#catalogue) : ({ kind: "rebuild" } as const);
      if (plan.kind === "rebuild" || !sent) {
        const version = ++this.#versions[track];
        this.#sent[track] = { version, slots: structuredClone(slots), lanes: structuredClone(trackLanes) };
        this.#send({ type: "rack", track, version, json: rackJson(slots), lanes: trackLanes });
        continue;
      }
      if (plan.kind === "set") {
        for (const s of plan.sets) {
          this.#send({ type: "set", track, version: sent.version, slot: s.slot, param: s.param, value: s.value });
        }
        sent.slots = structuredClone(slots);
      }
      if (JSON.stringify(sent.lanes) !== JSON.stringify(trackLanes)) {
        sent.lanes = structuredClone(trackLanes);
        this.#send({ type: "lanes", version: sent.version, lanes: trackLanes });
      }
    }
  }

  play(playing: boolean): void {
    if (this.#dead) return;
    this.#send({ type: "play", playing });
  }

  seek(frame: number): void {
    if (this.#dead) return;
    this.#send({ type: "seek", frame });
  }

  loop(on: boolean, start: number, end: number): void {
    if (this.#dead) return;
    this.#send({ type: "loop", on, start, end });
  }

  listen(gains: Record<Source, number>): void {
    this.#send({ type: "listen", gains });
  }

  get totalLatency(): number {
    return Math.max(this.latency.vocal, this.latency.beat) + this.latency.master;
  }
}
