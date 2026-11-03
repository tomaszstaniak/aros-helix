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
//! Each step prints a line before and after, so the last line shows where it stopped.
use std::io::Write;

fn say(s: &str) {
    let mut out = std::io::stdout();
    let _ = writeln!(out, "probe: {s}");
    let _ = out.flush();
}

fn catch() {
    say("catch: before");
    let r = std::panic::catch_unwind(|| panic!("deliberate"));
    say(&format!("catch: after, caught = {}", r.is_err()));
}

fn main() {
    let mode = std::env::args().nth(1).unwrap_or_default();
    say(&format!("main start, mode {mode}"));
    match mode.as_str() {
        "spawn" | "tcatch" => {
            let tcatch = mode == "tcatch";
            let handle = std::thread::Builder::new()
                .name("probe".into())
                .stack_size(16 * 1024 * 1024)
                .spawn(move || {
                    say("thread: running");
                    if tcatch {
                        catch();
                    }
                    7
                });
            say(&format!("spawned: {}", handle.is_ok()));
            let code = handle.unwrap().join();
            say(&format!("joined: {:?}", code.as_ref().ok()));
        }
        "catch" => catch(),
        "tokio" => {
            let file = std::env::args().nth(2).unwrap_or_default();
            let rounds: u64 = std::env::args().nth(3).and_then(|n| n.parse().ok()).unwrap_or(3);
            let editor = std::thread::Builder::new()
                .stack_size(16 * 1024 * 1024)
                .spawn(move || {
                    let rt = tokio::runtime::Builder::new_multi_thread().enable_all().build().unwrap();
                    rt.block_on(async move {
                        for round in 0..rounds {
                            let n = tokio::fs::read(&file).await.map(|d| d.len());
                            let m = tokio::spawn(async { tokio::task::spawn_blocking(|| 1).await }).await;
                            say(&format!("round {round}: read {:?}, nested {:?}", n.map_err(|e| e.to_string()), m.is_ok()));
                            tokio::time::sleep(std::time::Duration::from_secs(12)).await;
                        }
                    });
                    drop(rt);
                    say("runtime dropped");
                })
                .unwrap();
            say(&format!("editor thread joined: {}", editor.join().is_ok()));
        }
        "fs" | "fsc" => {
            let file = std::env::args().nth(2).unwrap_or_default();
            let rounds: usize = std::env::args().nth(3).and_then(|n| n.parse().ok()).unwrap_or(20);
            let width = if mode == "fsc" { 4 } else { 1 };
            for round in 0..rounds {
                let handles: Vec<_> = (0..width)
                    .map(|_| {
                        let file = file.clone();
                        std::thread::spawn(move || std::fs::read(&file).map(|d| d.len()))
                    })
                    .collect();
                for h in handles {
                    let r = h.join();
                    if !matches!(r, Ok(Ok(_))) {
                        say(&format!("round {round}: {:?}", r.map(|x| x.map_err(|e| e.to_string()))));
                    }
                }
                if round % 10 == 9 {
                    say(&format!("rounds done: {}", round + 1));
                }
            }
        }
        _ => say("modes: spawn, catch, tcatch"),
    }
    say("main end");
}
