//! Diagnostic: the steps hx takes before its editor runs, one at a time.
//!
//!   thread-probe spawn     start a thread with a 16 MiB stack (as hx does) and join it
//!   thread-probe catch     panic inside catch_unwind on the main task
//!   thread-probe tcatch    the same inside the spawned thread
//!   thread-probe fs FILE N  N rounds: a thread reads FILE and ends (as Tokio's
//!                          blocking pool does for file IO), joined each round
//!   thread-probe fsc FILE N the same with four such threads at a time
//!   thread-probe tokio FILE N  as Helix: a multi-thread Tokio runtime on a 16 MiB
//!                          thread; N rounds of tokio::fs::read, each followed by
//!                          12 s idle so the blocking-pool thread ends on its own
//!
//! Each step prints a line before and after, so the last line shows where it
//! stopped, and every round prints its result. Any failed step (spawn, join,
//! read, a nested task, a panic that was not caught) makes the exit status 10;
//! an unknown mode or bad arguments give 20. The last line before "main end"
//! is "result: ok" or "result: N failed".
use std::io::Write;

fn say(s: &str) {
    let mut out = std::io::stdout();
    let _ = writeln!(out, "probe: {s}");
    let _ = out.flush();
}

/// Panics inside catch_unwind; true when the panic was caught.
fn catch() -> bool {
    say("catch: before");
    let r = std::panic::catch_unwind(|| panic!("deliberate"));
    say(&format!("catch: after, caught = {}", r.is_err()));
    r.is_err()
}

fn file_and_rounds(default: u64) -> Result<(String, u64), String> {
    let file = std::env::args().nth(2).ok_or("missing FILE")?;
    let rounds = match std::env::args().nth(3) {
        None => default,
        Some(n) => n.parse().map_err(|_| format!("bad round count {n:?}"))?,
    };
    Ok((file, rounds))
}

fn main() {
    let mode = std::env::args().nth(1).unwrap_or_default();
    say(&format!("main start, mode {mode}"));
    let mut failed = 0u64;
    let mut check = |ok: bool, what: &str| {
        say(&format!("{} {what}", if ok { "ok" } else { "FAIL" }));
        if !ok {
            failed += 1;
        }
    };
    match mode.as_str() {
        "spawn" | "tcatch" => {
            let tcatch = mode == "tcatch";
            let handle = std::thread::Builder::new()
                .name("probe".into())
                .stack_size(16 * 1024 * 1024)
                .spawn(move || {
                    say("thread: running");
                    if tcatch && !catch() {
                        return 0;
                    }
                    7
                });
            match handle {
                Err(e) => check(false, &format!("spawn: {e}")),
                Ok(handle) => {
                    let code = handle.join();
                    check(matches!(code, Ok(7)), &format!("joined: {:?}", code.as_ref().ok()));
                }
            }
        }
        "catch" => check(catch(), "panic caught on the main task"),
        "tokio" => {
            let (file, rounds) = match file_and_rounds(3) {
                Ok(v) => v,
                Err(e) => usage(&e),
            };
            let editor = std::thread::Builder::new()
                .stack_size(16 * 1024 * 1024)
                .spawn(move || {
                    let rt = match tokio::runtime::Builder::new_multi_thread().enable_all().build() {
                        Ok(rt) => rt,
                        Err(e) => {
                            say(&format!("FAIL runtime: {e}"));
                            return 1;
                        }
                    };
                    let bad = rt.block_on(async move {
                        let mut bad = 0u64;
                        for round in 0..rounds {
                            let n = tokio::fs::read(&file).await.map(|d| d.len());
                            let m = tokio::spawn(async { tokio::task::spawn_blocking(|| 1).await }).await;
                            let ok = n.is_ok() && matches!(m, Ok(Ok(1)));
                            say(&format!(
                                "{} round {round}: read {:?}, nested {:?}",
                                if ok { "ok" } else { "FAIL" },
                                n.map_err(|e| e.to_string()),
                                m.map(|r| r.map_err(|e| e.to_string())).map_err(|e| e.to_string())
                            ));
                            bad += u64::from(!ok);
                            tokio::time::sleep(std::time::Duration::from_secs(12)).await;
                        }
                        bad
                    });
                    drop(rt);
                    say("runtime dropped");
                    bad
                });
            match editor {
                Err(e) => check(false, &format!("editor thread spawn: {e}")),
                Ok(editor) => {
                    let r = editor.join();
                    check(r.is_ok(), &format!("editor thread joined: {}", r.is_ok()));
                    if let Ok(bad) = r {
                        failed += bad;
                    }
                }
            }
        }
        "fs" | "fsc" => {
            let (file, rounds) = match file_and_rounds(20) {
                Ok(v) => v,
                Err(e) => usage(&e),
            };
            let width = if mode == "fsc" { 4 } else { 1 };
            for round in 0..rounds {
                let handles: Vec<_> = (0..width)
                    .map(|_| {
                        let file = file.clone();
                        std::thread::Builder::new().spawn(move || std::fs::read(&file).map(|d| d.len()))
                    })
                    .collect();
                let mut results = Vec::new();
                for h in handles {
                    let r = match h {
                        Err(e) => Err(format!("spawn: {e}")),
                        Ok(h) => match h.join() {
                            Err(_) => Err("join: thread panicked".to_string()),
                            Ok(Err(e)) => Err(format!("read: {e}")),
                            Ok(Ok(n)) => Ok(n),
                        },
                    };
                    results.push(r);
                }
                let ok = results.iter().all(|r| r.is_ok());
                say(&format!("{} round {round}: {results:?}", if ok { "ok" } else { "FAIL" }));
                failed += results.iter().filter(|r| r.is_err()).count() as u64;
            }
        }
        _ => usage(&format!("unknown mode {mode:?}")),
    }
    say(&if failed == 0 { "result: ok".to_string() } else { format!("result: {failed} failed") });
    say("main end");
    std::process::exit(if failed == 0 { 0 } else { 10 });
}

fn usage(why: &str) -> ! {
    say(&format!("{why}; modes: spawn, catch, tcatch, fs FILE N, fsc FILE N, tokio FILE N"));
    std::process::exit(20);
}
