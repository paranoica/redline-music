// The audio graph of the page: one AudioContext at the track's rate and the engine's node.

import type { Catalogue } from "../core/diff.ts";
import { EngineClient, type PortLike } from "./client.ts";

export interface Host {
  ctx: AudioContext;
  client: EngineClient;
  // stops the sound and closes the context with its worklet
  dispose(): Promise<void>;
}

export async function closeGraph(ctx: AudioContext, node: AudioWorkletNode): Promise<void> {
  node.port.onmessage = null;
  node.onprocessorerror = null;
  node.disconnect();
  node.port.close();
  if (ctx.state !== "closed") await ctx.close();
}

export async function startHost(rate: number, catalogue: Catalogue): Promise<Host> {
  // at the stems' own rate nothing is resampled between the stems and the output
  const ctx = new AudioContext({ sampleRate: rate, latencyHint: "interactive" });
  await ctx.audioWorklet.addModule(new URL("worklet.js", document.baseURI));
  const node = new AudioWorkletNode(ctx, "redline-engine", {
    numberOfInputs: 0,
    numberOfOutputs: 1,
    outputChannelCount: [2],
  });
  node.connect(ctx.destination);
  try {
    return await connect(ctx, node, catalogue);
  } catch (error) {
    await closeGraph(ctx, node).catch(() => {});
    throw error;
  }
}

async function connect(ctx: AudioContext, node: AudioWorkletNode, catalogue: Catalogue): Promise<Host> {
  const response = await fetch(new URL("redline_dsp.wasm", document.baseURI));
  if (!response.ok) throw new Error(`the engine's wasm did not load (${response.status})`);
  const bytes = await response.arrayBuffer();
  let client: EngineClient | null = null;
  const ready = new Promise<void>((resolve, reject) => {
    node.port.onmessage = (event: MessageEvent<{ type: string; message?: string }>) => {
      if (event.data.type === "ready") resolve();
      else if (event.data.type === "error") reject(new Error(event.data.message ?? "the engine did not start"));
    };
    // the processor threw past its own guard: the browser stops calling it
    node.onprocessorerror = (event: Event) => {
      const message = event instanceof ErrorEvent && event.message ? event.message : "the audio processor threw";
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
  client = new EngineClient(port, catalogue);
  return { ctx, client, dispose: () => closeGraph(ctx, node) };
}
