//! timer-probe: do timed waits end on time on AROS, without an outside wake-up?
//!
//!   timer-probe
//!
//! Each step waits 300 ms and prints how long it took. A watchdog thread
//! gives each step 3 s; after that it prints STUCK and wakes the waiter from
//! outside (a condvar notify or a spawned Tokio task), so a wait that only
//! ends on such a wake-up shows about 3000 ms instead of hanging.
//!
//! Steps: the clocks (CLOCK_MONOTONIC and gettimeofday, the two time bases a
//! pthread condvar can be given); std's park_timeout; parking_lot's
//! Condvar::wait_for (Tokio's parker with its parking_lot feature, as in hx);
//! Tokio's sleep on a current-thread and on a multi-thread runtime (hx uses
//! the latter); std::fs::canonicalize of PROGDIR:. Exit status 0 when every
//! wait ended within 1 s, 5 otherwise.
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

const WAIT: Duration = Duration::from_millis(300);
const LIMIT: Duration = Duration::from_secs(3);
static LATE: AtomicU32 = AtomicU32::new(0);

fn report(step: &str, took: Duration) {
    let ok = took < Duration::from_secs(1);
    if !ok {
        LATE.fetch_add(1, Ordering::SeqCst);
    }
    println!("timer-probe {step}: {} ms {}", took.as_millis(), if ok { "ok" } else { "LATE" });
}

/// Runs `wake` once if `done` is still false after LIMIT.
fn watchdog(step: &'static str, done: Arc<AtomicBool>, wake: impl FnOnce() + Send + 'static) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let start = Instant::now();
        while start.elapsed() < LIMIT {
            if done.load(Ordering::SeqCst) {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        if !done.load(Ordering::SeqCst) {
            println!("timer-probe {step}: STUCK after {} ms, waking it from outside", LIMIT.as_millis());
            wake();
        }
    })
}

fn clocks() {
    let mut mono = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    let mut real = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    let rm = unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut mono) };
    let rr = unsafe { libc::clock_gettime(libc::CLOCK_REALTIME, &mut real) };
    let mut tv = libc::timeval { tv_sec: 0, tv_usec: 0 };
    let rt = unsafe { libc::gettimeofday(&mut tv, std::ptr::null_mut()) };
    println!("timer-probe clocks: CLOCK_MONOTONIC {}.{:09} ({rm}), CLOCK_REALTIME {}.{:09} ({rr}), gettimeofday {}.{:06} ({rt})",
             mono.tv_sec, mono.tv_nsec, real.tv_sec, real.tv_nsec, tv.tv_sec, tv.tv_usec);
}

fn std_park() {
    let start = Instant::now();
    std::thread::park_timeout(WAIT);
    report("std park_timeout", start.elapsed());
}

fn parking_lot_condvar() {
    let pair = Arc::new((parking_lot::Mutex::new(false), parking_lot::Condvar::new()));
    let done = Arc::new(AtomicBool::new(false));
    let p2 = pair.clone();
    let dog = watchdog("parking_lot wait_for", done.clone(), move || {
        *p2.0.lock() = true;
        p2.1.notify_all();
    });
    let start = Instant::now();
    {
        let mut woken = pair.0.lock();
        // One wait: a timeout or the watchdog's notify ends it.
        if !*woken {
            let _ = pair.1.wait_for(&mut woken, WAIT);
        }
    }
    let took = start.elapsed();
    done.store(true, Ordering::SeqCst);
    let _ = dog.join();
    report("parking_lot wait_for", took);
}

fn tokio_sleep(name: &'static str, rt: tokio::runtime::Runtime) {
    let done = Arc::new(AtomicBool::new(false));
    let handle = rt.handle().clone();
    let dog = watchdog(name, done.clone(), move || {
        // An outside wake-up: any spawned task unparks the runtime.
        handle.spawn(async {});
    });
    let start = Instant::now();
    rt.block_on(async {
        // As in hx, the sleeping future runs in a spawned task and the
        // caller waits for its message.
        let (tx, rx) = tokio::sync::oneshot::channel();
        tokio::spawn(async move {
            tokio::time::sleep(WAIT).await;
            let _ = tx.send(());
        });
        let _ = rx.await;
    });
    let took = start.elapsed();
    done.store(true, Ordering::SeqCst);
    let _ = dog.join();
    report(name, took);
}

fn main() {
    clocks();
    std_park();
    parking_lot_condvar();
    tokio_sleep("tokio current-thread sleep",
                tokio::runtime::Builder::new_current_thread().enable_time().build().unwrap());
    tokio_sleep("tokio multi-thread sleep",
                tokio::runtime::Builder::new_multi_thread().enable_time().build().unwrap());
    match std::fs::canonicalize("PROGDIR:timer-probe") {
        Ok(p) => println!("timer-probe canonicalize PROGDIR:timer-probe: {}", p.display()),
        Err(e) => println!("timer-probe canonicalize PROGDIR:timer-probe: error {e}"),
    }
    let late = LATE.load(Ordering::SeqCst);
    println!("timer-probe end: {late} late");
    std::process::exit(if late == 0 { 0 } else { 5 });
}
