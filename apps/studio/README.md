# Studio page

The real-time studio: the vocal, beat and master racks run in an AudioWorklet on the
`redline_dsp` core built for WebAssembly, so a moved control is heard at once. The exact
render still goes through the Python engine on the server (`apps/redline/studio/server.py`),
which serves the built page from `dist/`.

## Build

Needs Node 22.18 or newer (the tests run the TypeScript sources directly).

```sh
npm --prefix apps/studio ci          # typescript and esbuild, nothing else
npm --prefix apps/studio run build   # dist/: index.html, app.js, worklet.js, styles.css, redline_dsp.wasm
npm --prefix apps/studio run watch   # rebuild app.js, worklet.js and styles.css on change
npm --prefix apps/studio run typecheck
npm --prefix apps/studio test
```

`src/engine/redline_dsp.{js,wasm}` come from `apps/redline-dsp/wasm/build.sh`; rebuild them
there when the Rust core changes, then build the page again.
`test/fixtures/racks-python.json` is what `redline.engine.live` makes for a set of settings;
`uv run python apps/studio/test/make_racks_fixture.py` writes it again after a change there.

## Layout

- `src/core/` — pure modules: settings to racks (a port of `live.racks`), rack diffs, loudness
  (BS.1770), undo history, latency alignment, WAV reading, formatting, control arithmetic.
- `src/engine/` — the engine the worklet runs (`core.ts`, testable in node), the page's side of
  its messages (`client.ts`), the AudioContext setup (`host.ts`).
- `src/worklet/processor.ts` — the AudioWorkletProcessor around the engine.
- `src/ui/` — the waveform, faders, rack editor, chain panels and meters; `src/main.ts` wires them.
