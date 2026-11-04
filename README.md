# Helix for AROS

[Helix](https://helix-editor.com), the modal text editor, built as a native
AROS x86_64 program. It runs in [Limpet](https://github.com/tomaszstaniak/limpet),
the AROS terminal.

This repository holds the port, not a copy of Helix: the pinned upstream
release, the patches applied to it and to a few crates it uses, and the build
script that turns them into an AROS executable.

| | |
|---|---|
| Helix | 25.07.1 |
| Port | aros3 |
| Target | AROS x86_64 ABIv11 (AROS One) |
| Terminal | Limpet 0.9.0 |
| Syntax highlighting | Rust and WebAssembly text |

## What works

Opening, editing and saving files, including UTF-8 text and Polish letters
typed on a Polish keymap; search, selections, yank and paste, the usual keys;
Helix's own colour theme; resizing the window; quitting back to a working
Shell, or closing the window. Settings go to `ENVARC:helix/`, the log to
`T:helix/`.

Iteration 3 includes wat_server 0.11.0 for WebAssembly text: live diagnostics,
hover, symbols and formatting through LSP. Start `hx`; the launcher supplies
the required stack and finds the server next to the editor.

[Download aros3](https://github.com/tomaszstaniak/aros-helix/releases/tag/aros3).
Separate formatters, `:sh`, `:pipe`, debug adapters and the system clipboard
remain unavailable. A hung server can block shutdown. An unresolved memory
corruption affecting Limpet was observed on AROS One; this release does not
claim to fix it. See the release notes before using it on important files.

See [using Helix on AROS](docs/README.md).

## Start here

- [Documentation](docs/README.md): installing and using the AROS build.
- [Development](docs/development.md): building it yourself.
- [Patches](docs/patching.md): what is changed, and how the series is reproduced.

## Project boundaries

- `upstreams.json` pins Helix and the Rust grammar; `upstream/` and `work/` are
  local checkouts and are not tracked.
- `patches/helix/`, `patches/crates/<crate>-<version>/` and
  `patches/aros-pthread/` hold every change as numbered patches with an
  explicit `series`.
- `scripts/hx.py` materialises the work copies, exports and verifies the
  patches, and builds and packages hx.
- `src/grammars/` is the small C file the grammar scanners need on AROS.
- `experiments/` holds the probes used to check the terminal layer.

The Rust environment (std for AROS, the libc bindings) comes from
[rust-aros](https://github.com/tomaszstaniak/rust-aros). Helix is licensed
under MPL-2.0, and so are the patches to it.
