# Experimental LSP series (Helix-LSP)

Applies on top of `patches/helix` (base: its last patch, "AROS: say that a
configured formatter was not run"). It is not part of the accepted preview
series and is never promoted; `scripts/hx.py build --lsp-experimental` builds
it with `--cfg aros_lsp_cooperative`, and `package --lsp-experimental` packs it
as `Helix-<version>-lsp-experimental<N>-<target>.zip` with the `Helix-LSP`
drawer, a language server and `packaging/lsp-experimental`.

To build: materialize as usual, then in `work/helix` create a branch from
`aros` and `git am` this series.

| patch | origin | what |
|---|---|---|
| 0001 | process-bridge 0001 (2026-11-01) | `helix_stdx::process` over `std::process`; pipes as Tokio files with a 1-byte buffer |
| 0002 | process-bridge 0002 | the editor runs on the program's main task; vfork from a pthread fails on AROS One (`__vfork+0x70`); needs `Stack 16777216` |
| 0003 | process-bridge 0003 | multi-thread runtime kept; formatter jobs polled on the main task |
| 0004 | new, 2026-11-04 | with `aros_lsp_cooperative` only commands marked `language_server()` (helix-lsp) start; formatters, `:sh`, `:pipe`, debug adapters stay refused; dropping a live server waits for it |

Not taken from the bridge: owner service, forced stop (v1 only, need a
patched posixc 0.47), non-blocking pipe IO (AROS One rejects O_NONBLOCK),
background shell commands.

Verification: 2026-11-04 on AROS One 1.3 (v11-1), archive
lsp-experimental1, with wat_server 0.11.0; see the private evidence run
20261104-163126-dc2491e6. v1: built only, not run.
