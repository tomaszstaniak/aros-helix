Helix 25.07.1 for AROS x86_64 (ABIv11), port release 1
=======================================================

Helix is a modal text editor (https://helix-editor.com). This is a native
AROS build of the Helix 25.07.1 release for AROS One and other ABIv11
systems on x86_64.

Requirements
------------
- Limpet 0.6.1 or later. Start Helix in a Limpet window: the AROS console
  (CON:) does not understand the terminal sequences Helix uses.
- About 45 MB of disk space.

Installing
----------
Unpack the archive, for example to Work:, which gives Work:Helix/hx and
Work:Helix/runtime. Keep the runtime drawer next to hx. To call it as hx
from any Shell, add for example to S:User-Startup:

    Path Work:Helix ADD

Using
-----
    hx notes.txt          edit a file
    hx --tutor            the tutorial
    hx --health           what this build finds and supports

Quit with :q, or :wq to save first.

Settings (config.toml, languages.toml, your own themes) go to ENVARC:helix,
the log to T:helix/helix.log. Files are read and written as UTF-8. Letters
beyond ASCII are typed as the keymap gives them; set the character set in
Locale preferences (for Polish: ISO-8859-2) so that Limpet sends the right
letters.

Not available yet
-----------------
- Language servers, debug adapters, formatters, :sh and :pipe. Helix cannot
  start other programs on AROS yet and says so.
- Git status in the gutter.
- The system clipboard. Yank and paste work inside Helix (y, p).
- Syntax highlighting for languages other than Rust.

Licence
-------
Helix is distributed under the Mozilla Public License 2.0 (LICENSE.txt).
THIRD-PARTY.txt lists the other components built into hx and their
licences. The AROS changes and how to build this program:
https://github.com/tomaszstaniak/aros-helix
