# LSP series

Applies on top of `patches/helix` (base: its last patch, "AROS: say that a
configured formatter was not run"). `scripts/hx.py build --lsp-experimental`
builds it with `--cfg aros_lsp_cooperative`. Port iteration 3 is this series:
`package --lsp-experimental --iteration 3` packs
`dist/Helix-25.07.1-aros3-<abi>.zip` (drawer `Helix`: the launcher `hx`, the
editor `hx-editor`, `wat_server`, `packaging/iteration`). The older
`package --lsp-experimental --candidate N` packs the 2026-11-04 experiment
(`Helix-LSP` drawer, `packaging/lsp-experimental`). The series name is
historical.

To build: materialize as usual, then in `work/helix` create a branch from
`aros` and `git am` this series.

| patch | origin | what |
|---|---|---|
| 0001 | process-bridge 0001 (2026-11-01) | `helix_stdx::process` over `std::process`; pipes as Tokio files with a 1-byte buffer |
| 0002 | process-bridge 0002 | the editor runs on the program's main task; vfork from a pthread fails on AROS One (`__vfork+0x70`); needs `Stack 16777216` |
| 0003 | process-bridge 0003 | multi-thread runtime kept; formatter jobs polled on the main task |
| 0004 | new, 2026-11-04 | with `aros_lsp_cooperative` only commands marked `language_server()` (helix-lsp) start; formatters, `:sh`, `:pipe`, debug adapters stay refused; dropping a live server waits for it |
| 0005 | iteration 3 | a bare server command not on the path is looked up in `HELIX_PROGDIR` (set by the launcher); host tests |
| 0006 | iteration 3 | status-line messages for a server that does not start (missing command configured by path, spawn error), fails to initialize, or stops unexpectedly; "Loaded N files" does not replace them |
| 0007 | iteration 3 | at quit with servers running: terminal restored first, "waiting for the language servers to exit..."; after a shutdown timeout, how many are still waited for |
| 0008 | iteration 3 | hx-editor refuses to start without the launcher (it would overflow a Shell's stack) |
| 0009 | iteration 3 | the WAT grammar is linked (tree-sitter-wasm, directory wat) |
| 0010 | iteration 3 | stdin writes fail with BrokenPipe after the child's stdout ended: on AROS One a write to a pipe without a reader waits for ever, and hx hung at quit after a crashed server and an edit; host tests |

Not taken from the bridge: owner service, forced stop (v1 only, need a
patched posixc 0.47), non-blocking pipe IO (AROS One rejects O_NONBLOCK),
background shell commands.

Verification: 0001-0004 on 2026-11-04 on AROS One 1.3 (v11-1), archive
lsp-experimental1, with wat_server 0.11.0 (private evidence run
20261104-163126-dc2491e6). 0005-0010: see the iteration 3 runs in the
private evidence. v1: built only, not run.

The automatic redraw of server messages comes from
`patches/crates/parking_lot_core-0.9.11`, not from this series.
