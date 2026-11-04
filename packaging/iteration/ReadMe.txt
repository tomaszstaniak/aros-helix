Helix 25.07.1 for AROS, port iteration 3
========================================

Helix is a modal text editor (https://helix-editor.com). This is a native
AROS build of the Helix 25.07.1 release for
@SYSTEM@.
It is an early version: use it on files you have copies of.

This iteration can start a language server. It includes one, wat_server
0.11.0 for WebAssembly text files (.wat), which gives Helix diagnostics,
hover, symbols and formatting for .wat files.

Requirements
------------
- Limpet 0.9.0 (@LIMPET@ from
  https://github.com/tomaszstaniak/limpet/releases). Start Helix in a
  Limpet window: the AROS console (CON:) does not understand the terminal
  sequences Helix uses.
- About 65 MB of disk space.

Installing
----------
Unpack the archive anywhere, for example to SYS: (or Work:, if your system
has it), which gives the drawer SYS:Helix. Keep its files together:

    UnZip Helix-25.07.1-aros3-abiv11.zip -d SYS:

To call Helix as hx from any Shell, add to S:User-Startup (with your
drawer):

    Path SYS:Helix ADD

Start Helix with hx. hx is a small program that runs the editor
(hx-editor) with the 16 MB stack it needs; you do not set a stack
yourself, and hx-editor is not started directly. No settings need to be
changed: Helix finds wat_server in its own drawer.

Using
-----
    hx notes.txt          edit a file
    hx --tutor            the tutorial
    hx --health wat       what this build finds for .wat files

Quit with :q, or :wq to save first; :q! quits without saving.

Settings (config.toml, languages.toml, your own themes) go to ENVARC:helix,
the log to T:helix/helix.log. Files are read and written as UTF-8.

Trying the language server
--------------------------
    Copy SYS:Helix/example.wat RAM:
    hx RAM:example.wat

After a moment Helix marks line 4, "local.get $c", with the server's error
(there is no $c), and the unused $b with a warning. Change $c to $b and
the marks go away. Then try:

    space k       what the server knows about the name under the cursor
    space s       the symbols of the file
    :format       format the file; for .wat this asks the language
                  server, no separate formatter program is run
    :w            save
    :o other.wat  open a second file; :bc closes the current one
    :lsp-restart  start the server again, for example after it stopped

If the server cannot be started or stops, the status line says so with
the server's name; the document stays open and can be edited and saved.

Turning the language server off
-------------------------------
- For one session: :lsp-stop.
- For good: rename or delete wat_server in the Helix drawer.
- Or in ENVARC:helix/config.toml (create the file if there is none):

      [editor.lsp]
      enable = false

  If the file exists, first keep a copy:
      Copy ENVARC:helix/config.toml ENVARC:helix/config.toml.bak
  To undo, copy it back, or delete the two lines (or the file, if you
  created it).

Limitations
-----------
- One language server was tested: wat_server for .wat files. Other servers
  may or may not work; none is included, and rust-analyzer does not run
  on AROS.
- Only language servers are started. Separate formatter programs, :sh,
  :pipe and debug adapters still report "Helix for AROS starts language
  servers only" and nothing is started. :format works for .wat because
  the language server formats it.
- AROS cannot stop a program from outside. When you quit, Helix asks the
  server to end and waits for it: the status line shows that it is
  waiting. A server that hangs keeps Helix from returning to the Shell
  until the server ends by itself, and :lsp-restart cannot end it either.
  wat_server ends normally within a few seconds.
- A language server configured by name only that is not installed is
  skipped without a message, as in Helix on other systems; one
  configured by its path that is missing is reported.
- Syntax highlighting for Rust and WebAssembly text only. Large Rust files
  open without highlighting.
- No system clipboard (yank and paste work inside Helix). Screen updates
  are slower than on other systems.
- On AROS One, in tests of earlier Helix builds, Limpet's own program
  code was found overwritten twice, and Limpet once stopped with a
  Software Failure. The cause is not known and is not fixed. If Limpet or
  Helix stops with a Software Failure, reboot before you continue.

Licence
-------
Helix is distributed under the Mozilla Public License 2.0 (LICENSE.txt);
THIRD-PARTY.txt lists the components built into hx-editor, and licenses/
holds the licences of its syntax grammars. wat_server is distributed
under the MIT licence (wat_server-LICENSE.txt); wat_server-THIRD-PARTY.txt
lists its components. Sources and build instructions:
https://github.com/tomaszstaniak/aros-helix and
https://github.com/g-plane/wasm-language-tools (wat_server 0.11.0).
