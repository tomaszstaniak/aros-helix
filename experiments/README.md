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
