#!/usr/bin/env bash
# Regenerates core/src/faust/<name>.rs from every core/faust/<name>.dsp (Faust 2.88).
# With a directory argument it writes there instead, which is how the test diffs them.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=${1:-$here/../src/faust}
for dsp in "$here"/*.dsp; do
    name=$(basename "$dsp" .dsp)
    {
        echo "// Generated from core/faust/$name.dsp by core/faust/generate.sh; do not edit."
        faust -lang rust -rnt -rnlm -light -ftz 2 -cn "${name^}" "$dsp"
    } >"$out/$name.rs"
done
