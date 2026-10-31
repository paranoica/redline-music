// Builds the pages into dist/: index.html, app.js, worklet.js, beat-worklet.js, styles.css,
// redline_dsp.wasm, the beat editor's beat.html, beat.js and beat.css, the song screen's daw.html, daw.js and
// daw.css, and the older song page's song.html, song.js, song.css and flip.css.
import { copyFile, mkdir, rm } from "node:fs/promises";
import * as esbuild from "esbuild";

const watch = process.argv.includes("--watch");
const dist = new URL("./dist/", import.meta.url);
const src = new URL("./src/", import.meta.url);

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });

async function copyStatic() {
  await copyFile(new URL("index.html", src), new URL("index.html", dist));
  await copyFile(new URL("beat.html", src), new URL("beat.html", dist));
  await copyFile(new URL("song.html", src), new URL("song.html", dist));
  await copyFile(new URL("daw.html", src), new URL("daw.html", dist));
  await copyFile(new URL("engine/redline_dsp.wasm", src), new URL("redline_dsp.wasm", dist));
}

const common = { bundle: true, format: "esm", target: "es2022", logLevel: "info", legalComments: "none" };
const builds = [
  { ...common, entryPoints: ["src/page.ts"], outfile: "dist/app.js", platform: "browser" },
  // the emscripten glue has a node branch that imports node: modules; it never runs in a worklet
  { ...common, entryPoints: ["src/worklet/processor.ts"], outfile: "dist/worklet.js", platform: "browser", external: ["node:*"] },
  { ...common, entryPoints: ["src/worklet/beat-processor.ts"], outfile: "dist/beat-worklet.js", platform: "browser", external: ["node:*"] },
  // the page makes event tables on its own copy of the wasm, through the same glue
  { ...common, entryPoints: ["src/beat/page.ts"], outfile: "dist/beat.js", platform: "browser", external: ["node:*"] },
  // the song page bundles both surfaces, so it carries the same glue exceptions
  { ...common, entryPoints: ["src/song/page.ts"], outfile: "dist/song.js", platform: "browser", external: ["node:*"] },
  // the song screen plays through the studio engine and the beat engine, so it carries the glue too
  { ...common, entryPoints: ["src/daw/page.ts"], outfile: "dist/daw.js", platform: "browser", external: ["node:*"] },
  { ...common, entryPoints: ["src/styles/daw.css"], outfile: "dist/daw.css", format: undefined },
  { ...common, entryPoints: ["src/styles/song.css"], outfile: "dist/song.css", format: undefined },
  { ...common, entryPoints: ["src/styles/main.css"], outfile: "dist/styles.css", format: undefined },
  { ...common, entryPoints: ["src/styles/beat.css"], outfile: "dist/beat.css", format: undefined },
  { ...common, entryPoints: ["src/styles/flip.css"], outfile: "dist/flip.css", format: undefined },
];

await copyStatic();
if (watch) {
  for (const options of builds) await (await esbuild.context(options)).watch();
  console.log("watching src/ (the html pages and the wasm are copied once; restart after changing them)");
} else {
  await Promise.all(builds.map((options) => esbuild.build(options)));
}
