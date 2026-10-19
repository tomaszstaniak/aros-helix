---
type: guide
updated: 2026-10-19
---

# Development

## Supported scope

| Target | State |
|---|---|
| x86_64 ABIv11 (AROS One 1.3) | built and run |
| x86_64 mainline (ABIv1) | not built |
| aarch64 | not built |

ABIv11 comes first because the terminal (Limpet) and the Rust environment are
verified there. Mainline needs its own SDK, toolchain and test run.

## Requirements

- The x86_64 AROS ABIv11 cross compiler and SDK (GCC 10.5).
- A deadwood2/AROS checkout at `5376f09bc1ed0c36a12fbb096406013f8a046e89`: the
  build compiles two objects of its `compiler/pthread` with this port's
  `patches/aros-pthread` (see that directory's README).
- [rust-aros](https://github.com/tomaszstaniak/rust-aros) at the commit named in
  your `local.json`, and a std toolchain made from it:

      AROS_RUST_BASE=nightly rust-aros/std-groundwork/std-patch/make-std-toolchain.sh \
          /new/sysroot aros-helix

  It requires `rustc 1.100.0-nightly (0dfb098f3 2026-08-31)` as the base.
- Python 3.11 or later, Git, and the crates in Cargo's cache: run
  `cargo fetch` once in `work/helix`.

## Configuration

Copy `local.json.example` to `local.json` (ignored by Git) and fill in the
paths. Every key can also be given as an environment variable; run
`python3 scripts/hx.py config` to see the names and the values in use.

## Commands

    git clone https://github.com/helix-editor/helix upstream/helix
    git clone https://github.com/tree-sitter/tree-sitter-rust upstream/tree-sitter-rust
    git -C upstream/tree-sitter-rust checkout 1f63b33efee17e833e0ea29266dd3d713e27e321
    python3 scripts/hx.py materialize   # work/helix and work/crates from the series
    python3 scripts/hx.py build         # build/v11/target/x86_64-aros/release/hx
    python3 scripts/hx.py package       # dist/Helix-<version>-abiv11.zip
    python3 scripts/hx.py verify        # every series rebuilds its work copy

The build stages a copy of the committed work tree, so commit in `work/` before
building. `build --probe term-probe` builds `experiments/term-probe` with the
same profile instead.

## Build profile

`panic=unwind` (a panic on the editor thread then ends the program from the
main task), `getrandom_backend="custom"` (Helix supplies it on AROS) and
`tokio_unstable` (from Helix's own configuration, which the build replaces). The
patched crates replace the registry ones with `patch.crates-io`. The C parts
(tree-sitter's runtime, the grammars) are built without PIC.

## Tests

Helix's own tests run on the host as usual (`cargo test` in `work/helix`). The
crossterm AROS source has host tests:

    cd work/crates/crossterm-0.28.1
    cargo test --lib --no-default-features --features events,event-stream,bracketed-paste,aros-event-source,libc -- event::source::unix::aros

On AROS, `experiments/term-probe` shows what the terminal layer reports and
`experiments/winsize-fds` which descriptors give the window size.
