# Vendored C++

Copied unmodified; `build.rs` compiles them through `cpp/stretch.cpp` with the `cc` crate.

| Folder | Source | Version | Licence |
|---|---|---|---|
| `signalsmith-stretch/` | https://github.com/Signalsmith-Audio/signalsmith-stretch, `signalsmith-stretch.h` | commit 88061fd4eda56a2353c5d65c74aa3f4ccf76f9da (tag 1.4.0 + 1, 2026-10-22; the header's own `version` says 1.3.2) | MIT, `signalsmith-stretch/LICENSE.txt` |
| `signalsmith-linear/` | https://github.com/Signalsmith-Audio/linear, `fft.h` and `stft.h` only (all stretch includes without platform FFT flags) | tag 0.6.6, commit 7bb1c8f36794d6980080ce817c91f8da8fe56a66 (the version stretch's CMakeLists pins) | MIT, `signalsmith-linear/LICENSE.txt` |

To update: clone both at the new commits, copy the same files over, update this table and run
`cargo test --release --workspace`.
