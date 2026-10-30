// What an AudioWorkletGlobalScope has that the DOM library does not declare.

declare abstract class AudioWorkletProcessor {
  readonly port: MessagePort;
  constructor(options?: unknown);
  abstract process(inputs: Float32Array[][], outputs: Float32Array[][], parameters: Record<string, Float32Array>): boolean;
}

declare function registerProcessor(name: string, processor: new (options?: unknown) => AudioWorkletProcessor): void;

declare const sampleRate: number;
