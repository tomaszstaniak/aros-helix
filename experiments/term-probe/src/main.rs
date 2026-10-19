//! Terminal-layer probe for the Helix port: prints the size crossterm sees,
//! then every event for up to a minute (q ends it), in raw mode with mouse
//! capture and bracketed paste, as Helix sets the terminal up.
use crossterm::{
    event::{DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste, EnableMouseCapture, Event, EventStream, KeyCode},
    execute, terminal,
};
use futures_util::StreamExt;
use std::{io::Write, time::{Duration, Instant}};

fn say(log: &mut std::fs::File, text: &str) {
    print!("{text}\r\n");
    std::io::stdout().flush().ok();
    writeln!(log, "{text}").ok();
}

async fn run(log: &mut std::fs::File) -> std::io::Result<()> {
    say(log, &format!("size before raw mode: {:?}", terminal::size()));
    terminal::enable_raw_mode()?;
    execute!(std::io::stdout(), EnableBracketedPaste, EnableMouseCapture)?;
    say(log, &format!("size in raw mode: {:?}", terminal::size()));
    say(log, &format!("keyboard enhancement: {:?}", terminal::supports_keyboard_enhancement()));
    let mut events = EventStream::new();
    let started = Instant::now();
    loop {
        let next = tokio::time::timeout(Duration::from_secs(60), events.next()).await;
        let Ok(Some(event)) = next else {
            say(log, &format!("no more events ({:?})", next.map(|e| e.is_some())));
            break;
        };
        let event = event?;
        say(log, &format!("{:>7.3} {event:?}", started.elapsed().as_secs_f32()));
        if let Event::Key(key) = event {
            if key.code == KeyCode::Char('q') {
                break;
            }
        }
    }
    drop(events);
    execute!(std::io::stdout(), DisableMouseCapture, DisableBracketedPaste)?;
    terminal::disable_raw_mode()?;
    say(log, "raw mode off");
    Ok(())
}

fn main() {
    let path = std::env::args().nth(1).unwrap_or_else(|| "RAM:term-probe.log".into());
    let mut log = std::fs::File::create(&path).expect("log file");
    let worker = std::thread::Builder::new().stack_size(4 << 20).spawn(move || {
        let runtime = tokio::runtime::Runtime::new().expect("runtime");
        let result = runtime.block_on(run(&mut log));
        drop(runtime);
        say(&mut log, &format!("result: {result:?}"));
    });
    worker.expect("thread").join().ok();
}
