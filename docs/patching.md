---
type: guide
updated: 2026-10-19
---

# Patches

`upstreams.json` pins the sources. Each changed source has a directory in
`patches/` with numbered patches and a `series` that gives their order:

| Directory | Base | Patches |
|---|---|---|
| `patches/helix/` | Helix 25.07.1 (`a05c151bb6e8`) | AROS build and runtime, settings locations, exit codes, AmigaDOS paths, linked grammars |
| `patches/crates/crossterm-0.28.1/` | crates.io archive | AROS event source and terminal queries |
| `patches/crates/tempfile-3.20.0/` | crates.io archive | named temporary files without rustix |
| `patches/crates/which-8.0.0/` | crates.io archive | executable check without rustix |
| `patches/crates/tree-house-bindings-0.2.1/` | crates.io archive | tree-sitter runtime on AROS, linked grammars |
| `patches/aros-pthread/` | deadwood2/AROS `5376f09bc1ed` `compiler/pthread` | ending threads cleanly (from the PdfCraft port) |

Crate archives are identified by the SHA-256 in `patches/crates/crates.json`;
the build refuses an archive that does not match.

Each patch starts with its reason, scope, upstream status and how it was
checked. None has been submitted upstream.

## Working on a patch

`work/helix` and `work/crates/<crate>-<version>` are Git work copies whose
commits after the base are the patches.

1. `python3 scripts/hx.py materialize` creates them from the series. It never
   replaces an existing work copy.
2. Edit and commit in the work copy, one commit per change, with the header
   fields the existing patches use.
3. `python3 scripts/hx.py export` writes the commits back to `patches/` and
   the `series` files.
4. `python3 scripts/hx.py verify` rebuilds every series in a temporary
   directory and checks that it gives the work copies' committed trees.

`upstream/` stays a clean checkout of the pin.
