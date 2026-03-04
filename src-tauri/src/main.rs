// Prevents additional console window on Windows in release
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backtester;
mod data_fetcher;
mod mesa;
mod optimizer;
mod pine;
mod types;

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use tauri::{Emitter, Manager, State};

// ============================================================
// App State
// ============================================================
#[derive(Default)]
struct AppState {
    /// Currently loaded price data
    bars: Mutex<Option<Vec<types::Bar>>>,
    /// Path to loaded data file
    data_path: Mutex<Option<PathBuf>>,
    /// Sweep progress (0.0 - 1.0)
    progress: Mutex<f64>,
    /// Whether a sweep is currently running
    running: Mutex<bool>,
}

// ============================================================
// IPC Commands — called from React frontend via invoke()
// ============================================================

/// Parse a Pine Script string, return extracted parameters as JSON
#[tauri::command]
fn parse_pine_script(code: String) -> Result<String, String> {
    let inputs = pine::extract_inputs(&code)?;
    serde_json::to_string(&inputs).map_err(|e| format!("Failed to serialize inputs: {}", e))
}

/// Run a Pine Script backtest sweep with the Pine interpreter
#[tauri::command]
async fn run_pine_sweep(
    pine_code: String,
    config_json: String,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<String, String> {
    // Check if already running
    {
        let running = state.running.lock().unwrap();
        if *running {
            return Err("Sweep already in progress".to_string());
        }
    }

    // Get bars
    let bars: Vec<pine::vm::BarData> = {
        let guard = state.bars.lock().unwrap();
        let raw_bars = guard.as_ref().ok_or("No data loaded. Load a CSV first.")?;
        raw_bars
            .iter()
            .map(|b| pine::vm::BarData {
                open: b.open,
                high: b.high,
                low: b.low,
                close: b.close,
                volume: b.volume,
                timestamp: 0, // TODO: parse timestamp from Bar
            })
            .collect()
    };

    // Parse the sweep config
    let config: PineSweepConfig =
        serde_json::from_str(&config_json).map_err(|e| format!("Invalid config: {}", e))?;

    // Mark as running
    *state.running.lock().unwrap() = true;
    *state.progress.lock().unwrap() = 0.0;

    let app_handle = app.clone();

    // Run sweep in background thread
    let result = tokio::task::spawn_blocking(move || {
        run_pine_sweep_inner(&pine_code, &bars, &config, |progress| {
            let _ = app_handle.emit("sweep-progress", progress);
        })
    })
    .await
    .map_err(|e| format!("Sweep task failed: {}", e))?
    .map_err(|e| format!("Sweep error: {}", e))?;

    // Mark as done
    *state.running.lock().unwrap() = false;
    *state.progress.lock().unwrap() = 1.0;

    serde_json::to_string(&result).map_err(|e| format!("Failed to serialize results: {}", e))
}

// ── Pine Sweep Types ────────────────────────────────────────

#[derive(Deserialize)]
struct PineSweepConfig {
    parameters: HashMap<String, serde_json::Value>,
    sweep_settings: PineSweepSettings,
}

#[derive(Deserialize)]
struct PineSweepSettings {
    fee_pct: f64,
    initial_capital: Option<f64>,
    top_n: Option<usize>,
    sort_by: Option<String>,
}

#[derive(Serialize)]
struct PineSweepResult {
    params: HashMap<String, serde_json::Value>,
    pf: f64,
    net: f64,
    dd: f64,
    wr: f64,
    trades: u32,
    gross_profit: f64,
    gross_loss: f64,
}

// ── Pine Sweep Core ─────────────────────────────────────────

fn run_pine_sweep_inner(
    pine_code: &str,
    bars: &[pine::vm::BarData],
    config: &PineSweepConfig,
    progress_callback: impl Fn(f64) + Send + Sync,
) -> Result<Vec<PineSweepResult>, String> {
    use rayon::prelude::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    // Parse and compile the Pine script once
    let program_ast = pine::parse_pine(pine_code)?;
    let mut compiler = pine::compiler::Compiler::new();
    let compiled = compiler.compile(&program_ast)?;

    // Build the parameter grid
    let (param_names, param_grid) = build_param_grid(&config.parameters)?;
    let total_combos = param_grid.len();

    if total_combos == 0 {
        return Err("No parameter combinations to sweep".to_string());
    }

    let fee_pct = config.sweep_settings.fee_pct;
    let initial_capital = config.sweep_settings.initial_capital.unwrap_or(10000.0);
    let top_n = config.sweep_settings.top_n.unwrap_or(200);
    let sort_by = config.sweep_settings.sort_by.clone().unwrap_or("pf".to_string());

    let completed = AtomicUsize::new(0);

    // Run all combos in parallel
    let mut results: Vec<PineSweepResult> = param_grid
        .par_iter()
        .filter_map(|combo| {
            // Create a fresh VM for each combo
            let mut vm = pine::vm::Vm::new(compiled.clone(), initial_capital, fee_pct);

            // Set input parameters
            for (i, val) in combo.iter().enumerate() {
                vm.set_input(&param_names[i], val.clone());
            }

            // Run backtest
            let result = vm.run(bars);

            // Report progress
            let done = completed.fetch_add(1, Ordering::Relaxed) + 1;
            if done % 100 == 0 || done == total_combos {
                progress_callback(done as f64 / total_combos as f64);
            }

            // Skip zero-trade results
            if result.total_trades == 0 {
                return None;
            }

            // Build param map for this combo
            let mut param_map = HashMap::new();
            for (i, name) in param_names.iter().enumerate() {
                param_map.insert(name.clone(), value_to_json(&combo[i]));
            }

            Some(PineSweepResult {
                params: param_map,
                pf: result.profit_factor,
                net: result.net_profit,
                dd: result.max_drawdown,
                wr: result.win_rate,
                trades: result.total_trades,
                gross_profit: result.gross_profit,
                gross_loss: result.gross_loss,
            })
        })
        .collect();

    // Sort results
    match sort_by.as_str() {
        "net" => results.sort_by(|a, b| b.net.partial_cmp(&a.net).unwrap_or(std::cmp::Ordering::Equal)),
        "wr" => results.sort_by(|a, b| b.wr.partial_cmp(&a.wr).unwrap_or(std::cmp::Ordering::Equal)),
        "trades" => results.sort_by(|a, b| b.trades.cmp(&a.trades)),
        _ => results.sort_by(|a, b| b.pf.partial_cmp(&a.pf).unwrap_or(std::cmp::Ordering::Equal)),
    }

    results.truncate(top_n);

    Ok(results)
}

/// Build a flat parameter grid from the config
fn build_param_grid(
    parameters: &HashMap<String, serde_json::Value>,
) -> Result<(Vec<String>, Vec<Vec<pine::vm::Value>>), String> {
    let mut param_names: Vec<String> = Vec::new();
    let mut param_values: Vec<Vec<pine::vm::Value>> = Vec::new();

    for (name, val) in parameters {
        let values = expand_pine_param(val);
        if !values.is_empty() {
            param_names.push(name.clone());
            param_values.push(values);
        }
    }

    // Build cartesian product
    let mut grid: Vec<Vec<pine::vm::Value>> = vec![vec![]];
    for values in &param_values {
        let mut new_grid = Vec::new();
        for existing in &grid {
            for val in values {
                let mut combo = existing.clone();
                combo.push(val.clone());
                new_grid.push(combo);
            }
        }
        grid = new_grid;
    }

    Ok((param_names, grid))
}

/// Expand a JSON parameter value into Pine Values
fn expand_pine_param(val: &serde_json::Value) -> Vec<pine::vm::Value> {
    match val {
        serde_json::Value::Array(arr) => {
            // Check if it's a range: [start, end, step]
            if arr.len() == 3
                && arr.iter().all(|v| v.is_number())
            {
                let start = arr[0].as_f64().unwrap_or(0.0);
                let end = arr[1].as_f64().unwrap_or(0.0);
                let step = arr[2].as_f64().unwrap_or(1.0);
                if step <= 0.0 || start > end {
                    return vec![pine::vm::Value::Float(start)];
                }
                let mut vals = Vec::new();
                let mut v = start;
                while v <= end + step * 0.001 {
                    vals.push(pine::vm::Value::Float((v * 100000.0).round() / 100000.0));
                    v += step;
                }
                if vals.is_empty() {
                    vals.push(pine::vm::Value::Float(start));
                }
                return vals;
            }
            // Otherwise it's explicit values
            arr.iter().map(json_to_pine_value).collect()
        }
        other => vec![json_to_pine_value(other)],
    }
}

fn json_to_pine_value(val: &serde_json::Value) -> pine::vm::Value {
    match val {
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                if i >= i32::MIN as i64 && i <= i32::MAX as i64 && n.as_f64() == Some(i as f64) {
                    pine::vm::Value::Int(i)
                } else {
                    pine::vm::Value::Float(n.as_f64().unwrap_or(0.0))
                }
            } else {
                pine::vm::Value::Float(n.as_f64().unwrap_or(0.0))
            }
        }
        serde_json::Value::Bool(b) => pine::vm::Value::Bool(*b),
        serde_json::Value::String(s) => pine::vm::Value::Str(s.clone()),
        serde_json::Value::Null => pine::vm::Value::Na,
        _ => pine::vm::Value::Na,
    }
}

fn value_to_json(val: &pine::vm::Value) -> serde_json::Value {
    match val {
        pine::vm::Value::Float(f) => serde_json::json!(f),
        pine::vm::Value::Int(i) => serde_json::json!(i),
        pine::vm::Value::Bool(b) => serde_json::json!(b),
        pine::vm::Value::Str(s) => serde_json::json!(s),
        pine::vm::Value::Na => serde_json::Value::Null,
    }
}

/// Load OHLCV CSV data from a file path
#[tauri::command]
fn load_data(path: String, state: State<AppState>) -> Result<DataInfo, String> {
    let bars = types::Bar::load_csv(&path).map_err(|e| format!("Failed to load data: {}", e))?;
    let info = DataInfo {
        bars: bars.len(),
        first_date: bars.first().map(|b| b.timestamp.clone()).unwrap_or_default(),
        last_date: bars.last().map(|b| b.timestamp.clone()).unwrap_or_default(),
        symbol: detect_symbol(&path),
        file_path: path.clone(),
        timeframe: detect_timeframe(&path),
    };
    *state.bars.lock().unwrap() = Some(bars);
    *state.data_path.lock().unwrap() = Some(PathBuf::from(&path));
    Ok(info)
}

#[derive(Serialize)]
struct DataInfo {
    bars: usize,
    first_date: String,
    last_date: String,
    symbol: String,
    file_path: String,
    timeframe: String,
}

fn detect_symbol(path: &str) -> String {
    let filename = std::path::Path::new(path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("UNKNOWN");
    // Try to extract symbol from filename like "BTCUSDT_5m_365d_bt"
    filename.split('_').next().unwrap_or("UNKNOWN").to_string()
}

fn detect_timeframe(path: &str) -> String {
    let filename = std::path::Path::new(path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("");
    // Try to extract timeframe from filename like "BTCUSDT_5m_365d_bt"
    let parts: Vec<&str> = filename.split('_').collect();
    if parts.len() >= 2 {
        let tf = parts[1];
        // Check if it looks like a timeframe
        if tf.ends_with('m') || tf.ends_with('h') || tf.ends_with('d') || tf.ends_with("Min") || tf.ends_with("Hour") || tf.ends_with("Day") {
            return tf.to_string();
        }
    }
    "unknown".to_string()
}

/// Run a parameter sweep with the given config JSON
#[tauri::command]
async fn run_sweep(
    config_json: String,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<String, String> {
    // Check if already running
    {
        let running = state.running.lock().unwrap();
        if *running {
            return Err("Sweep already in progress".to_string());
        }
    }

    // Get bars
    let bars = {
        let guard = state.bars.lock().unwrap();
        guard.clone().ok_or("No data loaded. Load a CSV first.")?
    };

    // Parse config
    let config: optimizer::SweepConfig =
        serde_json::from_str(&config_json).map_err(|e| format!("Invalid config: {}", e))?;

    // Mark as running
    *state.running.lock().unwrap() = true;
    *state.progress.lock().unwrap() = 0.0;

    let state_progress = Arc::new(Mutex::new(0.0f64));
    let sp = state_progress.clone();
    let app_handle = app.clone();

    // Run sweep in background thread
    let result = tokio::task::spawn_blocking(move || {
        optimizer::run_sweep(&bars, &config, |progress| {
            *sp.lock().unwrap() = progress;
            // Emit progress to frontend
            let _ = app_handle.emit("sweep-progress", progress);
        })
    })
    .await
    .map_err(|e| format!("Sweep task failed: {}", e))?
    .map_err(|e| format!("Sweep error: {}", e))?;

    // Mark as done
    *state.running.lock().unwrap() = false;
    *state.progress.lock().unwrap() = 1.0;

    // Return results as JSON
    serde_json::to_string(&result).map_err(|e| format!("Failed to serialize results: {}", e))
}

/// Stop a running sweep
#[tauri::command]
fn stop_sweep(state: State<AppState>) -> Result<(), String> {
    *state.running.lock().unwrap() = false;
    Ok(())
}

/// Get current sweep progress (0.0 - 1.0)
#[tauri::command]
fn get_progress(state: State<AppState>) -> f64 {
    *state.progress.lock().unwrap()
}

/// Fetch crypto OHLCV data (Binance/Coinbase — no API key needed)
#[tauri::command]
async fn fetch_crypto(
    symbol: String,
    interval: String,
    days: u32,
    source: String,   // "binance", "futures", "coinbase"
) -> Result<data_fetcher::FetchResult, String> {
    data_fetcher::fetch_crypto(&symbol, &interval, days, &source).await
}

/// Fetch futures OHLCV data (InsightSentry via RapidAPI)
#[tauri::command]
async fn fetch_futures(
    symbol: String,
    interval: String,
    days: u32,
    api_key: String,
) -> Result<data_fetcher::FetchResult, String> {
    data_fetcher::fetch_futures(&symbol, &interval, days, &api_key).await
}

/// Fetch stock/ETF OHLCV data (Alpaca)
#[tauri::command]
async fn fetch_stocks(
    symbol: String,
    interval: String,
    days: u32,
    api_key: String,
    api_secret: String,
) -> Result<data_fetcher::FetchResult, String> {
    data_fetcher::fetch_stocks(&symbol, &interval, days, &api_key, &api_secret).await
}

/// Save results CSV to a file
#[tauri::command]
fn save_results(path: String, csv_data: String) -> Result<(), String> {
    std::fs::write(&path, &csv_data).map_err(|e| format!("Failed to save: {}", e))
}

/// Load a sweep config JSON file
#[tauri::command]
fn load_config(path: String) -> Result<String, String> {
    std::fs::read_to_string(&path).map_err(|e| format!("Failed to read config: {}", e))
}

/// Save a sweep config JSON file
#[tauri::command]
fn save_config(path: String, config: String) -> Result<(), String> {
    std::fs::write(&path, &config).map_err(|e| format!("Failed to save config: {}", e))
}

/// Get app version
#[tauri::command]
fn get_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

// ============================================================
// Distributed Sweep — Coordinator
// ============================================================

#[derive(Serialize, Deserialize, Clone)]
struct WorkerInfo {
    address: String,     // "192.168.1.100:9876"
    name: String,
    status: String,      // "idle", "running", "done", "error"
    progress: f64,
    combos: usize,
}

/// Check if a worker is reachable and get its info
#[tauri::command]
async fn check_worker(address: String) -> Result<WorkerInfo, String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .build()
        .map_err(|e| e.to_string())?;

    let resp = client
        .get(format!("http://{}/health", address))
        .send()
        .await
        .map_err(|e| format!("Cannot reach {}: {}", address, e))?;

    let data: serde_json::Value = resp.json().await.map_err(|e| e.to_string())?;

    Ok(WorkerInfo {
        address: address.clone(),
        name: data["name"].as_str().unwrap_or("Unknown").to_string(),
        status: "idle".to_string(),
        progress: 0.0,
        combos: 0,
    })
}

/// Send data CSV to a worker
#[tauri::command]
async fn send_data_to_worker(
    address: String,
    state: State<'_, AppState>,
) -> Result<String, String> {
    let bars = state.bars.lock().unwrap().clone()
        .ok_or("No data loaded")?;

    // Convert bars back to CSV string
    let mut csv = String::from("timestamp,open,high,low,close,volume\n");
    for bar in &bars {
        csv.push_str(&format!("{},{},{},{},{},{}\n",
            bar.timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume));
    }

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(120))
        .build()
        .map_err(|e| e.to_string())?;

    let resp = client
        .post(format!("http://{}/load-data", address))
        .body(csv)
        .send()
        .await
        .map_err(|e| format!("Failed to send data to {}: {}", address, e))?;

    let body = resp.text().await.map_err(|e| e.to_string())?;
    Ok(body)
}

/// Start a sweep chunk on a remote worker
#[tauri::command]
async fn start_worker_sweep(
    address: String,
    config_json: String,
    chunk_start: usize,
    chunk_end: usize,
) -> Result<String, String> {
    let config: serde_json::Value = serde_json::from_str(&config_json)
        .map_err(|e| e.to_string())?;

    let payload = serde_json::json!({
        "config": config,
        "chunk_start": chunk_start,
        "chunk_end": chunk_end,
    });

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .map_err(|e| e.to_string())?;

    let resp = client
        .post(format!("http://{}/sweep", address))
        .json(&payload)
        .send()
        .await
        .map_err(|e| format!("Failed to start sweep on {}: {}", address, e))?;

    let body = resp.text().await.map_err(|e| e.to_string())?;
    Ok(body)
}

/// Poll worker status
#[tauri::command]
async fn poll_worker(address: String) -> Result<WorkerInfo, String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .build()
        .map_err(|e| e.to_string())?;

    let resp = client
        .get(format!("http://{}/status", address))
        .send()
        .await
        .map_err(|e| format!("Cannot reach {}: {}", address, e))?;

    let data: serde_json::Value = resp.json().await.map_err(|e| e.to_string())?;

    Ok(WorkerInfo {
        address: address.clone(),
        name: data["name"].as_str().unwrap_or("Unknown").to_string(),
        status: if data["running"].as_bool().unwrap_or(false) { "running" } else { "done" }.to_string(),
        progress: data["progress"].as_f64().unwrap_or(0.0),
        combos: data["total_combos"].as_u64().unwrap_or(0) as usize,
    })
}

/// Fetch results from a worker
#[tauri::command]
async fn get_worker_results(address: String) -> Result<String, String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| e.to_string())?;

    let resp = client
        .get(format!("http://{}/results", address))
        .send()
        .await
        .map_err(|e| format!("Cannot reach {}: {}", address, e))?;

    let body = resp.text().await.map_err(|e| e.to_string())?;
    Ok(body)
}

/// Stop a worker's sweep
#[tauri::command]
async fn stop_worker(address: String) -> Result<(), String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .build()
        .map_err(|e| e.to_string())?;

    client
        .post(format!("http://{}/stop", address))
        .send()
        .await
        .map_err(|e| format!("Cannot reach {}: {}", address, e))?;

    Ok(())
}

// ============================================================
// Main
// ============================================================
fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            parse_pine_script,
            run_pine_sweep,
            load_data,
            run_sweep,
            stop_sweep,
            get_progress,
            fetch_crypto,
            fetch_futures,
            fetch_stocks,
            save_results,
            load_config,
            save_config,
            get_version,
            check_worker,
            send_data_to_worker,
            start_worker_sweep,
            poll_worker,
            get_worker_results,
            stop_worker,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
