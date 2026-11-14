## Helix 25.07.1 for AROS, iteration 4

Helix now also runs on mainline AROS: on x86_64 PCs and on the Raspberry Pi. Iteration 3 for ABIv11 systems (AROS One) stays as it is; this iteration adds no changes for it.

| System | Archive |
| --- | --- |
| mainline AROS (ABIv1), x86_64 | `Helix-25.07.1-aros4-abiv1.zip` |
| native AROS on the Raspberry Pi (ABIv1, AArch64) | `Helix-25.07.1-aros4-raspi.zip` |
| AROS One and other ABIv11 systems, x86_64 | [iteration 3](https://github.com/tomaszstaniak/aros-helix/releases/tag/aros3) |

### What each build has

- The editor: modal editing, multiple selections, splits, syntax highlighting for Rust and WebAssembly text, the tutorial (`hx --tutor`).
- `hx` is the one program to start: it runs the editor with the 16 MB stack it needs.
- No language servers yet on mainline. The ABIv11 build starts one (wat_server); on mainline the editor stops responding when a server is started, so these builds start none and include none.

### Installation

Requires [Limpet](https://tomaszstaniak.github.io/limpet/) 0.10.1 or later (`apkg install limpet`): start Helix in a Limpet window.

    UnZip Helix-25.07.1-aros4-abiv1.zip -d SYS:
    Path SYS:Helix ADD
    hx notes.txt

The Raspberry Pi system has no UnZip: unpack the archive on another computer and copy the `Helix` drawer to the card.

### Limitations

- No language servers, formatter programs, `:sh`, `:pipe` or debug adapters: Helix reports that the program was not started.
- Syntax highlighting for Rust and WebAssembly text only. Large Rust files open without highlighting.
- No system clipboard (yank and paste work inside Helix). Screen updates are slower than on other systems.
- Raspberry Pi: tested under QEMU's Pi model, not on a real Raspberry Pi. If Helix fails with an internal error, the program stops at once and the window may be left in the editor's screen mode; close the Limpet window.

### Checked

From these archives, with Limpet 0.10.1: start with a Rust file, an edit saved and compared, quit with return code 0, a second session with a split and a second file, no process left afterwards.

- mainline x86_64: nightly build of 2026-11-13 in QEMU;
- Raspberry Pi: nightly build of 2026-11-10 in QEMU (raspi3b).
