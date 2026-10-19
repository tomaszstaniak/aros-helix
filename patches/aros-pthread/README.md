# aros-pthread

Two fixes to the ABIv11 pthread library (deadwood2/AROS
`5376f09bc1ed0c36a12fbb096406013f8a046e89`, `compiler/pthread`), taken
unchanged from the PdfCraft port (aros-artcraft `pdfcraft/patches/aros-pthread`,
commit a5fa10e). Without them a Rust program can hang at exit after a thread it
detached has finished, or exit while a detached thread is still in its
epilogue. Rust detaches every thread whose handle is dropped; crossterm's event
stream thread is one.

`scripts/hx.py build` first rebuilds the SDK's `libpthread.a` from the
unpatched source and checks that the objects match, then links the two patched
objects ahead of the SDK's library. The patch headers give the details and the
upstream status.
