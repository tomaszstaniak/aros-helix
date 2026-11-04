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

## timing: aggregated counters (0004)

Question: when Down is held in Limpet, where does the time go, and do keys
pile up?
Run: apply `timing/0004-*.patch` alone (not with 0001-0003, which touch the
same lines) to the `aros` branch, build with
`HX_BUILD_ROOT=build/v11-perf python3 scripts/hx.py build`, start
`hx -v --log RAM:h.log FILE`. Each second with key input gives one `perf`
line: keys, Down keys, renders, lines moved and scrolled, and the time spent
handling events, composing and drawing (total and largest). Limpet's
counterpart is `make abiv11-perf` (`XTERM_PERF_LOG`).
Found (AROS One 1.3, Limpet 0.6.1, 2026-10-19): a scrolled screen is about
8 KB that Helix's line-buffered stdout writes in nine 1 KB pieces; Limpet
redrew after each piece, so Helix's draw took 165-215 ms per scrolled frame
and a held Down was handled at 5-6 keys a second while 16-18 arrived. With
Limpet redrawing after its output pauses (Limpet `d41d71e`) the draw took
25-34 ms per frame and the keys were handled at 8-18 a second (25 without
highlighting). Details in Limpet's `docs/evidence/2026-10-19-down-key/`.

## parse-budget

Tried and rejected: `parse-budget/0001-*.patch` gives the first parse 3 s on
AROS. The 230 KB file then opened highlighted, but each keystroke re-parsed
for 0.3-1 s and keys queued up. Kept for reference; not in the series.

## thread-probe

Question: why did hx fail at its first thread on mainline (ABIv1) nightly
20261031, and do threads that read files and end, or Tokio's runtime as
Helix uses it, fail there on their own?
Run: `python3 scripts/hx.py build --target aros-x86_64-abiv1-pc --probe
thread-probe`, then `thread-probe spawn|catch|tcatch|fs FILE N|fsc FILE N|tokio FILE N`.
Found (2026-11-03, v1 nightly 20261031): with rust-aros libc-aros 79324c2
`spawn` ended the creating task at PC 0, because mainline's `pthread_attr_t`
grew to 168 bytes (rust-aros 43b3798 fixes it). With the fix, spawn, `fs`,
`fsc` and `tokio` ended with status 0 after 50, 50 and 4 rounds; a panic in
a thread is not caught on v1 (`tcatch` aborts).
That version printed some failures (a failed read, join or nested task) and
still ended with status 0, so those runs do not exclude failed rounds. Since
2026-11-03 every round prints `ok` or `FAIL`, any failure gives status 10 and
an unknown mode or bad arguments 20 (also a round count of 0); keep the whole
output with the status. `fsm DIR N` starts each round's four reads together
(after every created thread waits at a gate) and ends with the number of rounds
in which all four reads were in progress at once; a result without overlap
says nothing about a race.

## modrace

Question: does a loaded program's debug information become visible before it
is complete, so that a task ending at that moment fails in
`Debug_UnregisterModule`?
Run: `x86_64-aros-gcc --sysroot=SDK -O2 -o modrace modrace/modrace.c`, then
`modrace CRASH FILE SECONDS` (public calls only: two processes load and unload
FILE while short `NP_Entry` processes end) or `modrace DETECT FILE SECONDS`
(walks debug.library's private module list; read-only). Status 0 means no
failure and every child gone from exec's task lists; 20 a usage error, a
child that did not start, or a child still listed 30 s after it returned. The
counts say how many children returned, not how many were reaped. Before
2026-11-03 night a failed second loader start left the first one running and
the end of the test was a fixed delay; runs before that change rely on the
crash itself, not on a clean end.
Found (2026-11-03): on AROS One 1.3 (debug.library 2.1, 01/30/26) `CRASH`
with hx candidate 2 (12 371 loadable sections) as FILE ended in a Software
Failure in `Debug_6_UnregisterModule` with RAX=0 and RSI=0x3052: a module with
12 371 segments listed before its segment array existed. The acceptance crash
was in the same loop (+0x16C against +0x169). Mainline nightly 20261031
(debug.library 2.3) ran the same 200 s without failure; mainline 1c25bf6c16
builds the array before listing the module. `DETECT` saw nothing in 54 loads,
so it cannot show the absence of the window. Builds since then use
`-Zfunction-sections=no` (7 loadable sections); that shortens the window but
does not close it: `CRASH` with such a build also failed, on a module of 6
segments.

## conprobe

Question: which console access pattern raises "unexpected DOS packet received"
(seen at hx's `:q` on mainline)? Run: `conprobe ONE|WORKER|PENDING N LOG`
(see the source): the Shell's own task in raw mode with WaitForChar, a second
process doing the same on the shared handles while the first writes, and mode
changes and writes while another process waits in WaitForChar. Every call is
recorded in memory with task, reply port, handle and handler port and written
to LOG at the end.
Found (2026-11-03, v1-2, older v1 base): all three pass in CON: and in Limpet
0.9.0, no alert. Not yet run on nightly 20261031, where hx raised it.

## cxx-exc and thread-probe unw

Question: why does no panic unwind on mainline? `exc.cpp` is a C++ throw and
catch; `thread-probe unw` calls `_Unwind_Find_FDE` and `_Unwind_Backtrace`.
Found (2026-11-03): with the mainline GCC 10.5 toolchain of 2026-09-02 both
abort (C++ after the throw; `_Unwind_Backtrace` before its first frame), in the
small and the large code model; `_Unwind_Find_FDE` works. That toolchain's
libgcc never fills `dwarf_reg_size_table` (AROS fixed its GCC patch in
52bab89955, 2026-09-13). `hx.py build` now links libgcc's `unwind-dw2.o`
rebuilt without that guard for ABIv1 (`build_unwind_fix`); with it C++ catches,
the backtrace walks 7 frames, and `catch` and `tcatch` catch the panic.

## conexit

Question: does hx's console pattern at exit raise the v1 DOS alert when run
again and again in one Shell? `conexit A|B|J MS [M|E]`: a detached pthread
(16 MiB stack) waits with WaitForChar(250 ms), reads, and sends the CONG
geometry packet after empty slices, as libc-aros's poll and TIOCGWINSZ do; the
console owner sets raw mode with tcsetattr, writes frames, then stops the
thread, restores cooked mode and returns without joining (A), in the other
order (B), or with a join (J). Layout M: the program's task is the console
owner; E: as hx, main creates a 16 MiB editor thread that owns the console and
creates the input thread, joins it and ends with exit(). Every call is checked
(status 5 when one failed after setup); each thread prints its role and task.
Found (2026-11-04, nightly 20261031, Limpet 0.9.0): layout M, 51 runs in one
Shell, all clean (before the checks were added). Layout E was not run: pkttrap
found the cause first, and it is not in the console path (see pkttrap).

## pkttrap

Question: which message does a task find on its process port when dos.library
raises "unexpected DOS packet received" (0x07000004), and who put it there?
Diagnostic only, for a disposable test machine: it patches exec with
SetFunction and stays resident until reset. `Run >NIL: pkttrap LOG NAME...`
watches tasks named `pthread thread #...` and Shell processes running NAME. It
records message traffic, list calls on their process ports, and opens and
closes of stdc/stdcio/posixc in a ring. It checks those ports' lists on every
patched call, and sets an x86 write watchpoint (DR0/DR7 through Supervisor,
kernel.resource handler for vector 1) on `ln_Succ` of the message last added
to such a port. The ring is written to LOG at the alert (then frozen) and on
CTRL-E; callers are decoded with debug.library. `pkttrap TEST` checks the
watchpoint. Build: `x86_64-aros-gcc --sysroot=SDK -O2 -o pkttrap pkttrap.c`.
Found (2026-11-04, nightly 20261031, Helix preview 2 candidate 5): the alert
is a use-after-free in the C runtime, not a stray packet. The editor thread's
own stdcio base, opened by its posixc base, caches its NIL: handles. A
`getpwuid_r()` from `std::env::home_dir` in that thread opens usergroup.library,
which holds a second reference to that posixc base. hx main closes usergroup
at exit, after the editor thread has freed those handles, and stdcio's
`__close_stdio` writes `fh_Flags` into them. That memory was the thread's last
UnLock packet; the write turned its `ln_Succ` into the port's list header.
Details: AROS-dev docs/platform/v1-posixc-worker-base-stdcio-uaf-20261104.md.

## home-probe

Question: what does `std::env::home_dir()` return on AROS, from the program's
task and from a pthread worker, with `HOME` unset, empty or set? Run: `python3
scripts/hx.py build --target <target> --probe home-probe`, then `home-probe`
in a Shell. Found (2026-11-04, v1 nightly 20261031, toolchain
aros-v1-x86-20261104 with rust-aros 321495a's std change): unset and empty
`HOME` (local or global) give `None` in both, `HOME=RAM:home` gives
`Some("RAM:home")`; `HOME` is not set on that nightly by default. The binary
no longer calls posixc's `getpwuid_r`, whose `usergroup.library` open from a
worker led to the v1 dos alert (see pkttrap).

## timer-probe

Question: do timed waits end on time on AROS without an outside wake-up?
Helix drew a language server's diagnostics only at the next key. Run:
`python3 scripts/hx.py build --target <target> --probe timer-probe`, then
`timer-probe` in a Shell. Found (2026-11-04, AROS One 1.3): std's
`park_timeout` ends on time; parking_lot's `Condvar::wait_for` and Tokio's
`sleep` (current-thread and multi-thread runtimes, with Tokio's parking_lot
feature as in hx) waited until the probe woke them from outside after 3 s.
AROS's pthread turns a condvar's absolute timeout into a relative one with
`gettimeofday()`, but parking_lot_core gives it a `CLOCK_MONOTONIC` time
(seconds since boot), which becomes a wait of many years. With
patches/crates/parking_lot_core-0.9.11 all waits end within 400 ms.
`std::fs::canonicalize("PROGDIR:...")` fails there with ENOENT, so the
launcher (src/launcher) passes its drawer to hx instead.

## mini-lsp

A small language server for failure tests (C, Content-Length framing over
stdio): `mini-lsp MODE LOG [SECONDS]` with the modes normal,
ignore-shutdown, spin, crash (exits after the document is opened), stall and
bad-init (answers initialize with an error). Built with the target's C
compiler and SDK, e.g. `x86_64-aros-gcc --sysroot=<SDK> -O2 -Wall -o
mini-lsp mini_lsp.c`; configured as a language server in a test
languages.toml.
