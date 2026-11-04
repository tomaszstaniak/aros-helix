Helix-LSP 25.07.1 for AROS: experimental language server support
================================================================

This is an EXPERIMENTAL build of Helix 25.07.1 for @SYSTEM@
that can start a language server. It is not a release. Use it next to the
regular Helix preview, on files you have copies of. The regular preview (hx
in the Helix drawer) is unchanged and still starts no programs.

Tested with one language server: wat_server 0.11.0 for WebAssembly text
files (.wat), included here. Other language servers, rust-analyzer among
them, have not been tried.

Requirements
------------
- Limpet 0.9.0 (@LIMPET@ from
  https://github.com/tomaszstaniak/limpet/releases). Start Helix-LSP in a
  Limpet window.
- About 60 MB of disk space.

Installing
----------
Unpack the archive to SYS:, which gives SYS:Helix-LSP. If you unpack it
elsewhere, change the path in hx-lsp and in languages.toml. Then copy the
language server setting:

    Copy SYS:Helix-LSP/languages.toml ENVARC:helix/languages.toml

(If you already have ENVARC:helix/languages.toml, add the lines of the
included file to it instead.)

Trying it
---------
In a Limpet window:

    Execute SYS:Helix-LSP/hx-lsp SYS:Helix-LSP/example.wat

hx-lsp sets a 16 MB stack for the Shell (this build runs the editor on the
Shell's own task) and starts SYS:Helix-LSP/hx. The example has a mistake
on line 4, "local.get $c". Wait a moment and press Esc: Helix marks the
line and shows the server's message (see Limitations for why the key
press is needed). Change $c to $b, press Esc, and the mark goes away.
Also try:

    space k       show what the server knows about the name under the cursor
    space s       the symbols of the file
    :format       format the file; for .wat this asks the language server,
                  no separate formatter program is run
    :w            save as usual
    :q            quit; Helix asks the server to shut down and waits for it

Turning it off
--------------
Delete the [language-server.wasm-language-tools] lines from
ENVARC:helix/languages.toml, or use the regular hx: it does not start the
server.

Limitations
-----------
- Experimental. Only language servers can be started. Formatters, :sh,
  :pipe and debug adapters still report that this build starts language
  servers only.
- Messages the server sends by itself, such as diagnostics, are drawn
  only at your next key press or mouse click: after opening or changing a
  file, press a key (Esc does nothing else) to see them. Answers to your
  own requests (space k, space s, :format) appear at once.
- If the language server cannot be started, for example because the path
  in languages.toml is wrong, the file opens without diagnostics and Helix
  shows no message.
- A language server cannot be stopped from outside on AROS. When you quit,
  Helix asks the server to shut down and waits until it has ended. A server
  that does not end (one that hangs or ignores the request) keeps Helix
  from returning to the Shell until it ends by itself, and it stays in the
  system until then; the screen does not change while Helix waits. In a
  test, a server that ignored the request kept Helix waiting until it
  ended 20 seconds later. Closing the Limpet window does not end it
  either.
- The other limitations of the Helix preview apply: no system clipboard,
  syntax highlighting for Rust only (.wat files are shown without colours),
  slower screen updates.
- On AROS One, Limpet's program code was twice found overwritten in tests of
  the Helix preview (one Software Failure in Limpet among seven boots). The
  cause is not known. If Limpet stops with a Software Failure, reboot before
  you continue.

Licence
-------
Helix is distributed under the Mozilla Public License 2.0 (LICENSE.txt);
THIRD-PARTY.txt lists the components built into hx. wat_server is
distributed under the MIT licence (wat-server-LICENSE.txt);
wat-server-THIRD-PARTY.txt lists its components. Sources and build
instructions: https://github.com/tomaszstaniak/aros-helix and
https://github.com/g-plane/wasm-language-tools (wat_server 0.11.0).
