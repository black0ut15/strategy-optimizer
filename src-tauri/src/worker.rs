//! Strategy Optimizer — Distributed Worker Node
//! 
//! Lightweight HTTP server that accepts sweep jobs and returns results.
//! Run on any machine with the data CSV accessible.
//!
//! Usage:
//!   strategy-worker --port 9876
//!   strategy-worker --port 9876 --name "Laptop"
//!
//! The coordinator (main app) sends:
//!   POST /sweep  { config, chunk_start, chunk_end, data_url }
//!   GET  /status
//!   GET  /health
//!   POST /stop

use std::io::Read;
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Instant;

use strategy_optimizer::{backtester, mesa, optimizer, types};

// ── Shared State ──────────────────────────────────────────────
struct WorkerState {
    name: String,
    running: AtomicBool,
    progress: AtomicUsize, // 0-10000 (basis points)
    total_combos: AtomicUsize,
    completed: AtomicUsize,
    results: Mutex<Vec<optimizer::SweepResult>>,
    should_stop: AtomicBool,
    bars: Mutex<Option<Vec<types::Bar>>>,
}

impl WorkerState {
    fn new(name: String) -> Self {
        Self {
            name,
            running: AtomicBool::new(false),
            progress: AtomicUsize::new(0),
            total_combos: AtomicUsize::new(0),
            completed: AtomicUsize::new(0),
            results: Mutex::new(Vec::new()),
            should_stop: AtomicBool::new(false),
            bars: Mutex::new(None),
        }
    }
}

// ── HTTP Handling ─────────────────────────────────────────────

fn read_request(stream: &mut TcpStream) -> Option<(String, String, String)> {
    let mut buf = [0u8; 65536];
    let mut total = Vec::new();

    // Read headers first
    loop {
        match stream.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => {
                total.extend_from_slice(&buf[..n]);
                if total.windows(4).any(|w| w == b"\r\n\r\n") {
                    break;
                }
            }
            Err(_) => break,
        }
    }

    let header_str = String::from_utf8_lossy(&total).to_string();
    let mut lines = header_str.lines();
    let first_line = lines.next()?;
    let parts: Vec<&str> = first_line.split_whitespace().collect();
    if parts.len() < 2 {
        return None;
    }

    let method = parts[0].to_string();
    let path = parts[1].to_string();

    // Get content-length
    let mut content_length: usize = 0;
    for line in header_str.lines() {
        if line.to_lowercase().starts_with("content-length:") {
            content_length = line.split(':').nth(1)
                .and_then(|s| s.trim().parse().ok())
                .unwrap_or(0);
        }
    }

    // Read body
    let header_end = total.windows(4)
        .position(|w| w == b"\r\n\r\n")
        .map(|p| p + 4)
        .unwrap_or(total.len());

    let body_so_far = total.len() - header_end;
    let mut body = total[header_end..].to_vec();

    while body.len() < content_length {
        match stream.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => body.extend_from_slice(&buf[..n]),
            Err(_) => break,
        }
    }

    let body_str = String::from_utf8_lossy(&body).to_string();
    Some((method, path, body_str))
}

fn send_response(stream: &mut TcpStream, status: u16, body: &str) {
    let status_text = match status {
        200 => "OK",
        400 => "Bad Request",
        409 => "Conflict",
        _ => "Internal Server Error",
    };
    let response = format!(
        "HTTP/1.1 {} {}\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\nAccess-Control-Allow-Headers: Content-Type\r\nContent-Length: {}\r\n\r\n{}",
        status, status_text, body.len(), body
    );
    use std::io::Write;
    let _ = stream.write_all(response.as_bytes());
}

fn handle_request(
    stream: &mut TcpStream,
    method: &str,
    path: &str,
    body: &str,
    state: &Arc<WorkerState>,
) {
    match (method, path) {
        ("OPTIONS", _) => {
            send_response(stream, 200, "");
        }

        ("GET", "/health") => {
            let resp = serde_json::json!({
                "status": "ok",
                "name": state.name,
                "version": env!("CARGO_PKG_VERSION"),
            });
            send_response(stream, 200, &resp.to_string());
        }

        ("GET", "/status") => {
            let running = state.running.load(Ordering::Relaxed);
            let progress = state.progress.load(Ordering::Relaxed) as f64 / 10000.0;
            let total = state.total_combos.load(Ordering::Relaxed);
            let completed = state.completed.load(Ordering::Relaxed);
            let result_count = state.results.lock().unwrap().len();

            let resp = serde_json::json!({
                "name": state.name,
                "running": running,
                "progress": progress,
                "total_combos": total,
                "completed": completed,
                "result_count": result_count,
            });
            send_response(stream, 200, &resp.to_string());
        }

        ("POST", "/load-data") => {
            // Receive CSV data directly in body
            let bars: Vec<types::Bar> = match types::Bar::load_csv_from_string(body) {
                Ok(b) => b,
                Err(e) => {
                    send_response(stream, 400, &format!("{{\"error\":\"{}\"}}", e));
                    return;
                }
            };
            let count = bars.len();
            *state.bars.lock().unwrap() = Some(bars);
            send_response(stream, 200, &format!("{{\"bars\":{}}}", count));
        }

        ("POST", "/sweep") => {
            if state.running.load(Ordering::Relaxed) {
                send_response(stream, 409, "{\"error\":\"Sweep already running\"}");
                return;
            }

            // Parse sweep job
            #[derive(serde::Deserialize)]
            struct SweepJob {
                config: optimizer::SweepConfig,
                chunk_start: usize,
                chunk_end: usize,
            }

            let job: SweepJob = match serde_json::from_str(body) {
                Ok(j) => j,
                Err(e) => {
                    send_response(stream, 400, &format!("{{\"error\":\"Invalid config: {}\"}}", e));
                    return;
                }
            };

            let bars = state.bars.lock().unwrap().clone();
            if bars.is_none() {
                send_response(stream, 400, "{\"error\":\"No data loaded. POST to /load-data first.\"}");
                return;
            }
            let bars = bars.unwrap();

            let total = job.chunk_end - job.chunk_start;
            state.total_combos.store(total, Ordering::Relaxed);
            state.completed.store(0, Ordering::Relaxed);
            state.progress.store(0, Ordering::Relaxed);
            state.running.store(true, Ordering::Relaxed);
            state.should_stop.store(false, Ordering::Relaxed);
            *state.results.lock().unwrap() = Vec::new();

            let state2 = Arc::clone(state);

            thread::spawn(move || {
                let start_time = Instant::now();
                println!("  Starting sweep: combos {}..{} ({} total)", 
                    job.chunk_start, job.chunk_end, total);

                let results = optimizer::run_sweep_chunk(
                    &bars,
                    &job.config,
                    job.chunk_start,
                    job.chunk_end,
                    |done, _total| {
                        state2.completed.store(done, Ordering::Relaxed);
                        let pct = (done as f64 / total as f64 * 10000.0) as usize;
                        state2.progress.store(pct, Ordering::Relaxed);
                    },
                    || state2.should_stop.load(Ordering::Relaxed),
                );

                let elapsed = start_time.elapsed();
                let rate = total as f64 / elapsed.as_secs_f64();
                println!("  Sweep complete: {} results in {:.1}s ({:.0} combos/sec)",
                    results.len(), elapsed.as_secs_f64(), rate);

                *state2.results.lock().unwrap() = results;
                state2.progress.store(10000, Ordering::Relaxed);
                state2.running.store(false, Ordering::Relaxed);
            });

            send_response(stream, 200, &format!("{{\"started\":true,\"combos\":{}}}", total));
        }

        ("GET", "/results") => {
            let results = state.results.lock().unwrap();
            let json = serde_json::to_string(&*results).unwrap_or("[]".to_string());
            send_response(stream, 200, &json);
        }

        ("POST", "/stop") => {
            state.should_stop.store(true, Ordering::Relaxed);
            send_response(stream, 200, "{\"stopped\":true}");
        }

        _ => {
            send_response(stream, 400, "{\"error\":\"Unknown endpoint\"}");
        }
    }
}

// ── Main ──────────────────────────────────────────────────────

fn main() {
    let args: Vec<String> = std::env::args().collect();

    let mut port: u16 = 9876;
    let mut name = hostname::get()
        .map(|h| h.to_string_lossy().to_string())
        .unwrap_or_else(|_| "Worker".to_string());

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--port" | "-p" => {
                i += 1;
                port = args.get(i).and_then(|s| s.parse().ok()).unwrap_or(9876);
            }
            "--name" | "-n" => {
                i += 1;
                name = args.get(i).cloned().unwrap_or(name);
            }
            "--help" | "-h" => {
                println!("Strategy Optimizer Worker");
                println!();
                println!("Usage: strategy-worker [OPTIONS]");
                println!();
                println!("Options:");
                println!("  --port, -p <PORT>   Listen port (default: 9876)");
                println!("  --name, -n <NAME>   Worker name (default: hostname)");
                println!("  --help, -h          Show this help");
                return;
            }
            _ => {}
        }
        i += 1;
    }

    let state = Arc::new(WorkerState::new(name.clone()));

    let addr = format!("0.0.0.0:{}", port);
    let listener = TcpListener::bind(&addr).expect(&format!("Cannot bind to {}", addr));

    println!("╔══════════════════════════════════════════════╗");
    println!("║       Strategy Optimizer — Worker Node       ║");
    println!("╠══════════════════════════════════════════════╣");
    println!("║  Name: {:<37} ║", name);
    println!("║  Port: {:<37} ║", port);
    println!("║  Addr: {:<37} ║", addr);
    println!("╠══════════════════════════════════════════════╣");
    println!("║  Endpoints:                                  ║");
    println!("║    GET  /health     — liveness check         ║");
    println!("║    GET  /status     — progress & stats       ║");
    println!("║    POST /load-data  — send CSV data          ║");
    println!("║    POST /sweep      — start sweep chunk      ║");
    println!("║    GET  /results    — retrieve results        ║");
    println!("║    POST /stop       — abort current sweep    ║");
    println!("╚══════════════════════════════════════════════╝");
    println!();
    println!("Waiting for jobs from coordinator...");

    for stream in listener.incoming() {
        if let Ok(mut stream) = stream {
            let state = Arc::clone(&state);
            thread::spawn(move || {
                if let Some((method, path, body)) = read_request(&mut stream) {
                    handle_request(&mut stream, &method, &path, &body, &state);
                }
            });
        }
    }
}
