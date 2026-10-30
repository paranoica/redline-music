#!/usr/bin/env bash
# Builds the rack for the browser: apps/studio/src/engine/redline_dsp.{js,wasm}.
# Needs emsdk (EMSDK, default ~/.local/emsdk) and `rustup target add wasm32-unknown-emscripten`.
# CI has no emsdk: this build and `node --test apps/studio/test` run locally only for now.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
workspace="$(dirname "$here")"
repo="$(cd "$workspace/../.." && pwd)"
out="$repo/apps/studio/src/engine"

# shellcheck disable=SC1091
source "${EMSDK:-$HOME/.local/emsdk}/emsdk_env.sh" >/dev/null 2>&1

# panic messages carry source paths; keep the builder's home out of the module
export CARGO_TARGET_WASM32_UNKNOWN_EMSCRIPTEN_RUSTFLAGS="--remap-path-prefix=${CARGO_HOME:-$HOME/.cargo}=/cargo --remap-path-prefix=${RUSTUP_HOME:-$HOME/.rustup}=/rustup --remap-path-prefix=$repo=/redline"

cargo build --release --locked --manifest-path "$workspace/Cargo.toml" \
    --target wasm32-unknown-emscripten -p redline-dsp-wasm

mkdir -p "$out"
# A fixed heap, so the worklet's views of memory never go stale. At 48 kHz a usual rack takes
# a few MB, the heaviest effect (unison) 10 MB, a rack of the 32 heaviest about 55 MB; at
# 192 kHz four times that. 256 MB holds the studio's racks plus one rebuilt beside its old
# self. Running out aborts the module (Rust cannot fail an allocation softly), so the page
# must keep within it. Rust's std unwinds with wasm exceptions, hence -fwasm-exceptions.
em++ "$workspace/target/wasm32-unknown-emscripten/release/libredline_dsp_wasm.a" \
    -o "$out/redline_dsp.js" \
    -O3 -fwasm-exceptions \
    -sMODULARIZE -sEXPORT_ES6 -sEXPORT_NAME=createRedlineDsp \
    -sENVIRONMENT=web,worker,node \
    -sALLOW_MEMORY_GROWTH=0 -sINITIAL_MEMORY=256MB -sSTACK_SIZE=1MB \
    -sFILESYSTEM=0 \
    -sEXPORTED_FUNCTIONS=_rack_new,_rack_free,_rack_latency,_rack_process,_rack_set,_rack_reset,_effects_json,_rack_error,_malloc,_free \
    -sEXPORTED_RUNTIME_METHODS=HEAPF32,HEAPU8,UTF8ToString

ls -l "$out/redline_dsp.js" "$out/redline_dsp.wasm"
