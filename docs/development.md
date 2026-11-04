---
type: guide
updated: 2026-10-19
---

# Development

## Supported scope

| Target | State |
|---|---|
| x86_64 ABIv11 (AROS One 1.3), `aros-x86_64-abiv11-pc` | built and run |
| x86_64 mainline (ABIv1), `aros-x86_64-abiv1-pc` | built and run (nightly 20261031) |
| aarch64 | not built |

ABIv11 comes first because the terminal (Limpet) and the Rust environment are
verified there. Mainline needs its own SDK, toolchain and test run.

## Requirements

- The x86_64 AROS ABIv11 cross compiler and SDK (GCC 10.5).
- A deadwood2/AROS checkout at `5376f09bc1ed0c36a12fbb096406013f8a046e89`: the
  build compiles two objects of its `compiler/pthread` with this port's
  `patches/aros-pthread` (see that directory's README).
- [rust-aros](https://github.com/tomaszstaniak/rust-aros) at the commit in
  `upstreams.json` (`build_environment`), and a std toolchain made from it:

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
    python3 scripts/hx.py build         # build/aros-x86_64-abiv11-pc/target/x86_64-aros/release/hx
    python3 scripts/hx.py package --candidate 1   # dist/unreleased/Helix-<version>-candidate1-<target>.zip
    python3 scripts/hx.py promote --candidate 1 --accepted ACCEPTANCE.json   # after acceptance: dist/Helix-<version>-<target>.zip
    python3 scripts/hx.py verify        # every series rebuilds its work copy

For mainline add `--target aros-x86_64-abiv1-pc` to `config`, `build`,
`package` and `promote`. Its compiler, SDK, toolchain and rust-aros commit go
under `"targets": {"aros-x86_64-abiv1-pc": {...}}` in `local.json`. It needs a
rust-aros commit whose libc-aros has the 168-byte `pthread_attr_t` (43b3798
or later) and links the SDK's own libpthread.

Both targets' toolchains need std from rust-aros's `patch-std.py` with step
9g (321495a): on AROS `std::env::home_dir()` uses `HOME` only. Without it,
`home_dir()` in hx's editor thread calls posixc's `getpwuid_r`, which on v1
(nightly 20261031) leads to a use-after-free in the C runtime at exit and the
dos alert 0x07000004. Candidate 6 uses `aros-v1-x86-20261104` (rust-aros
c1b5e67: the candidate 5 sysroot base 0b7dca2 plus that step) and
`aros-helix-20261104` (the earlier v11 sysroot with the same change).

`package` writes candidates and their sums (`SHA256SUMS-<version>`) to
`dist/unreleased/` only. `package --unstripped` packs the same linked binary
without stripping it, as `...-candidate<N>-unstripped-<target>.zip`, for
diagnosis; it is never promoted. `promote` copies the tested candidate byte for
byte to `dist/` and adds it to `dist/SHA256SUMS`; neither replaces an existing
file. `promote` needs `--accepted`, the acceptance record: JSON with schema
`helix-acceptance-1`, the candidate's file name, SHA-256 and target, outcome
`pass`, and every scenario of `ACCEPTANCE_SCENARIOS` marked `pass` with the
aros-vm-runs `REPORT.json` of its run. Each named run must have passed on the
target's ABI with this archive among its inputs. A record that is not a pass
for this archive and target is refused, as is an archive whose unpacked files
differ from its contents list. Copies go to temporary names first; a journal
(`dist/.<name>.promoting`) marks a promotion in progress, so running the same
command again after a failure at any step finishes it, while an existing
archive without a journal is never replaced. `python3 scripts/test_promote.py`
checks the refusals and the resumption on the host.

For ABIv1 `build` also needs `gcc_build`, the host build directory of the GCC
that made the toolchain (`<AROS build>/bin/<host>`), when that toolchain's
libgcc never fills its DWARF register size table (AROS GCC patches before
52bab89955, 2026-09-13): every panic would abort. `build` then compiles
libgcc's `unwind-dw2.c` from that tree twice, unchanged (its disassembly and
relocations, `objdump -d -r`, must match the toolchain's object; the bytes are
not compared) and without the guard, and links the fixed object ahead
of libgcc. `build.json` records it as `unwinder`.

Before compiling, `build` checks libc-aros against the SDK (the ABI layout
gate): the SDK's compiler measures `sizeof`, `_Alignof` and `offsetof` of the
types and fields in `ABI_TYPES` and `ABI_FIELDS`, and a `cargo check` of
generated const assertions compares libc-aros with them. A difference stops the
build and names each layout. The measurements go to `abi-gate/sdk-layout.json`
in the build root and their hash to `build.json` (`sdk_layout`).
`--no-abi-gate` exists for diagnostic rebuilds of an earlier input set;
`package` refuses such a build.

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
