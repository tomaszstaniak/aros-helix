## Helix 25.07.1 for AROS, iteration 3

For AROS x86_64 ABIv11 (AROS One). Helix 25.07.1 can now start a language server: the archive includes wat_server 0.11.0, which gives diagnostics, hover, symbols and formatting for WebAssembly text files (.wat).

### New in this iteration

- Language servers. Helix starts the included wat_server for .wat files without any change to your settings.
- Diagnostics appear and update as soon as the server sends them. You no longer need to press a key to see them.
- Clear messages. If a language server cannot be started (for example a wrong path in `languages.toml`), fails to initialize or stops while you work, the status line says so and names the server. The document stays open and can be edited and saved. `:lsp-restart` starts the server again.
- Waiting at quit. If a language server is still running when you quit, hx says in the Shell window that it is waiting for it.
- Syntax highlighting for WebAssembly text, next to Rust.
- One program to start. Run `hx`: it starts the editor with the 16 MB stack it needs. You do not set a stack, and the drawer can be anywhere.

### Checked

On AROS One 1.3 (x86_64 ABIv11, in QEMU) with Limpet 0.9.0, using this archive installed as its ReadMe describes on a clean configuration:

- installing to `SYS:`;
- diagnostics appearing without a key press, and updating or clearing after an edit;
- hover and the symbol list;
- `:format` through the language server, with the saved bytes compared;
- two open documents, closing one and working on in the other;
- a wrong server path, a server that crashes, and a server that refuses to initialize, each with its message;
- `:sh`, `:pipe`, an external formatter and a debug adapter still refused;
- a normal quit, and a second session in the same boot;
- no language server left running afterwards.

The ABIv1 (mainline) build compiles but was not run.

### Limitations

- Only wat_server was tried. Other language servers are unqualified; rust-analyzer is not included or qualified by this release.
- `:format` for .wat files is done by the language server. Separate formatter programs, `:sh`, `:pipe` and debug adapters are still not started: Helix reports "Helix for AROS starts language servers only".
- This build does not forcibly terminate servers. When you quit, Helix waits until each language server has ended. A server that hangs keeps Helix from returning to the Shell until it ends by itself, and `:lsp-restart` cannot end it either. wat_server ends normally within a few seconds.
- The ReadMe says the status line shows this wait. In fact the notice "waiting for the language servers to exit..." is printed in the Shell window.
- A language server configured by name only that is not installed is skipped without a message, as in Helix on other systems.
- Syntax highlighting for Rust and WebAssembly text only. There is no system clipboard, and screen updates are slower than on other systems.
- On AROS One, in tests of earlier Helix builds, Limpet's own program code was found overwritten twice, and Limpet once stopped with a Software Failure. The cause is not known and is not fixed. If Limpet or Helix stops with a Software Failure, reboot before you continue.

- A write already in progress when a server dies can still hang shutdown. The workaround prevents later writes after stdout EOF; it does not cancel an in-flight write.
- The exact original v11 sysroot source revision is unknown, so bit-for-bit reproduction of the toolchain has not been established.

### Installation

Requires [Limpet 0.9.0](https://github.com/tomaszstaniak/limpet/releases): start Helix in a Limpet window.

    UnZip Helix-25.07.1-aros3-abiv11.zip -d SYS:
    Path SYS:Helix ADD

Then try the example:

    Copy SYS:Helix/example.wat RAM:
    hx RAM:example.wat

To turn the language server off, use `:lsp-stop` for one session, or rename `wat_server` in the Helix drawer. The ReadMe also describes a setting for this, with how to back up and restore your configuration.

### Sources and verification

The downloadable ZIP is the unchanged archive tested on AROS One: SHA-256 `3b81814cf8fda135d3d45a5d54ec1f3f23dcda509dd95a8a84a02ade78de5777`. Its ReadMe has the wait-message erratum described above. Helix patches and launcher sources are in this release tag. The supplementary source archive contains the wat_server port patches and Rust libc/std patch sources used by the port, from rust-aros revision 6dca821. It excludes private test records and is not a complete prebuilt SDK.
