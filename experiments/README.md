# Experiments

Probes used to settle questions about the terminal layer. Each is small and
stays exploratory; what they found is in the patches that came of it.

## term-probe

Question: what does crossterm (with this port's AROS patch) see in a Limpet
window: size, resize, keys, Esc, Alt, Ctrl, and does it leave raw mode
cleanly?
Run: `python3 scripts/hx.py build --probe term-probe`, copy
`build/v11/probe-target/x86_64-aros/release/term-probe` to AROS, run
`term-probe RAM:log.txt` in Limpet, press keys, `q` ends it.
Found (AROS One 1.3, Limpet 0.6.0): keys, Alt, Ctrl and a lone Esc arrive as
sent; the resize report arrives at once; the size query from a worker thread
failed (0x0), which led to the rust-aros TIOCGWINSZ fix; a Polish keymap's
letters arrived as Latin-1, which led to the Limpet 0.6.1 fix.

## winsize-fds

Question: which descriptors give the window size through the C library's
`ioctl(TIOCGWINSZ)`, and what does it return when it fails?
Run: build with the AROS compiler,
`x86_64-aros-gcc --sysroot=SDK -O2 -o winsize-fds winsize-fds.c`, and run it
in Limpet and in CON:.
Found: from the main task, descriptors 0 and 1 answer (24x80 in Limpet,
31x76 in CON:); descriptor 2 fails with a positive 9 instead of -1.

## timing

Question: where does Helix spend its time on AROS: first parse, update after
an edit, building a frame, writing it to the terminal?
Run: apply `timing/*.patch` to `work/helix` on a branch, build with
`HX_BUILD_ROOT=build/v11-timing python3 scripts/hx.py build`, start
`hx -v --log RAM:t.log FILE` and read the `timing` lines. The patches also
add `HX_DIAG_FAIL_WRITE` (a save fails after the file was truncated) and
`HX_DIAG_PARSE_TIMEOUT_MS` (another budget for the first parse). They are
diagnostic only and are never part of a release.
Found (AROS One 1.3 under QEMU TCG, 2026-10-19): drawing to the terminal
takes most of each frame (about 30-150 ms on average, up to 460 ms);
building it 7-25 ms. A 230 KB Rust file needs more than the 500 ms parse
budget (1.4-4.5 s with a longer one) and then 0.3-1 s per edit; a 27 KB file
0.05-0.1 s per edit. Guest speed differed up to 3x between boots.

## parse-budget

Tried and rejected: `parse-budget/0001-*.patch` gives the first parse 3 s on
AROS. The 230 KB file then opened highlighted, but each keystroke re-parsed
for 0.3-1 s and keys queued up. Kept for reference; not in the series.
