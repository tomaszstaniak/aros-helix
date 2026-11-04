//! home-probe: what std::env::home_dir() returns on AROS, from the program's
//! task and from a pthread worker (hx's editor runs in one), with HOME unset,
//! empty or set. Prints one line per caller and "home-probe end".
#![allow(deprecated)]

use std::{env, thread};

fn report(who: &str) -> String {
    format!("home-probe {who}: home_dir {:?}", env::home_dir())
}

fn main() {
    println!("home-probe HOME {:?}", env::var_os("HOME"));
    println!("{}", report("main"));
    let worker = thread::Builder::new()
        .stack_size(16 * 1024 * 1024)
        .spawn(|| report("thread"))
        .expect("spawn");
    println!("{}", worker.join().expect("join"));
    println!("home-probe end");
}
