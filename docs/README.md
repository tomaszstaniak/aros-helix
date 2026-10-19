---
type: overview
updated: 2026-10-19
---

# Using Helix on AROS

## Requirements

- AROS x86_64 ABIv11, such as AROS One 1.3.
- [Limpet](https://github.com/tomaszstaniak/limpet) 0.6.1 or later. Helix
  needs a terminal that understands xterm sequences; the AROS console (`CON:`)
  does not. With Limpet 0.6.0 the Shell receives stray input after Helix
  quits, and Polish letters arrive wrong.
- About 45 MB of disk space.

## Installing

Unpack the archive somewhere, for example to `Work:`, which gives
`Work:Helix/hx` and `Work:Helix/runtime/`. Keep `runtime` next to `hx`: it
holds the syntax queries, themes and the tutorial. To call it as `hx` from
any Shell, add the drawer to the path, for example in `S:User-Startup`:

    Path Work:Helix ADD

## Running

Open a Limpet window and start it with a file:

    hx notes.txt

`hx --version` and `hx --health` work from any Shell. `hx --tutor` opens the
tutorial. Quit with `:q`, or `:wq` to save first.

## Files

| What | Where |
|---|---|
| Settings (`config.toml`, `languages.toml`, own themes) | `ENVARC:helix/` |
| Log file | `T:helix/helix.log` |
| Runtime files shipped with hx | `runtime/` next to `hx` |

If the variable `HOME` is set, Helix uses the usual Unix places under it
instead. Paths can be AmigaDOS paths (`Work:Projects/notes.txt`) or relative
to the current drawer.

## Keyboard and characters

Helix uses the keys of the terminal: Esc, Alt (left Alt), Ctrl, the cursor and
function keys. Characters beyond ASCII are typed as the keymap gives them;
Limpet turns them into UTF-8 using the system character set chosen in Locale
preferences (`ENV:CHARSET`, for example ISO-8859-2 for Polish). Files are
read and written as UTF-8.

## Not available yet

- Language servers, debug adapters, formatters, `:sh`, `:pipe` and opening
  URLs: Helix cannot start other programs on AROS yet, and reports that.
- Git status in the gutter.
- The system clipboard: yank and paste work inside Helix (`y`, `p`).
- Syntax highlighting for languages other than Rust.
