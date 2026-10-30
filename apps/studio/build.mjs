// Builds the page into dist/: index.html, app.js, worklet.js, styles.css, redline_dsp.wasm.
import { copyFile, mkdir, rm } from "node:fs/promises";
import * as esbuild from "esbuild";

const watch = process.argv.includes("--watch");
const dist = new URL("./dist/", import.meta.url);
const src = new URL("./src/", import.meta.url);

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });

async function copyStatic() {
  await copyFile(new URL("index.html", src), new URL("index.html", dist));
  await copyFile(new URL("engine/redline_dsp.wasm", src), new URL("redline_dsp.wasm", dist));
}

const common = { bundle: true, format: "esm", target: "es2022", logLevel: "info", legalComments: "none" };
const builds = [
  { ...common, entryPoints: ["src/main.ts"], outfile: "dist/app.js", platform: "browser" },
  // the emscripten glue has a node branch that imports node: modules; it never runs in a worklet
  { ...common, entryPoints: ["src/worklet/processor.ts"], outfile: "dist/worklet.js", platform: "browser", external: ["node:*"] },
  { ...common, entryPoints: ["src/styles/main.css"], outfile: "dist/styles.css", format: undefined },
];

await copyStatic();
if (watch) {
  for (const options of builds) await (await esbuild.context(options)).watch();
  console.log("watching src/ (index.html and the wasm are copied once; restart after changing them)");
} else {
  await Promise.all(builds.map((options) => esbuild.build(options)));
}
