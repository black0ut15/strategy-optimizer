use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::process::Command;

/// Result of a data fetch operation
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FetchResult {
    pub bars: usize,
    pub csv_path: String,
    pub first_date: String,
    pub last_date: String,
    pub symbol: String,
}

/// Get the path to the bundled fetcher scripts.
/// In dev: src-tauri/fetchers/    In prod: next to exe in fetchers/
fn get_fetchers_dir() -> PathBuf {
    if let Ok(exe) = std::env::current_exe() {
        let prod_dir = exe.parent().unwrap_or(Path::new(".")).join("fetchers");
        if prod_dir.exists() {
            return prod_dir;
        }
    }
    let dev_dir = PathBuf::from("fetchers");
    if dev_dir.exists() {
        return dev_dir;
    }
    PathBuf::from("src-tauri").join("fetchers")
}

/// Get or create the data output directory (matches Python _DATA_HOME)
fn get_data_dir(subdir: &str) -> PathBuf {
    let base = if let Ok(appdata) = std::env::var("APPDATA") {
        PathBuf::from(appdata).join("StrategyOptimizer")
    } else if let Ok(home) = std::env::var("USERPROFILE") {
        PathBuf::from(home).join("StrategyOptimizer")
    } else {
        PathBuf::from(".")
    };
    let dir = base.join("data").join(subdir);
    std::fs::create_dir_all(&dir).ok();
    dir
}

/// Find Python executable
fn find_python() -> String {
    for cmd in &["python3", "python"] {
        if let Ok(output) = Command::new(cmd).arg("--version").output() {
            if output.status.success() {
                return cmd.to_string();
            }
        }
    }
    "python".to_string()
}

/// Fetch crypto OHLCV data via crypto_fetcher.py
/// source: "binance", "futures" (Binance USDT-M perps), "coinbase"
pub async fn fetch_crypto(
    symbol: &str,
    interval: &str,
    days: u32,
    source: &str,
) -> Result<FetchResult, String> {
    let script = get_fetchers_dir().join("crypto_fetcher.py");
    if !script.exists() {
        return Err(format!("crypto_fetcher.py not found at {:?}", script));
    }

    let python = find_python();
    let symbol_owned = symbol.to_string();
    let interval_owned = interval.to_string();
    let source_owned = source.to_string();
    let script_str = script.to_string_lossy().to_string();

    let output = tokio::task::spawn_blocking(move || {
        Command::new(&python)
            .arg(&script_str)
            .arg(&symbol_owned)
            .arg(&interval_owned)
            .arg(days.to_string())
            .arg(&source_owned)
            .arg("export")
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .output()
    })
    .await
    .map_err(|e| format!("Task failed: {}", e))?
    .map_err(|e| format!("Failed to run crypto_fetcher.py: {}", e))?;

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    if !output.status.success() {
        return Err(format!("crypto_fetcher.py failed:\n{}\n{}", stdout, stderr));
    }

    // Find the exported CSV
    let data_dir = get_data_dir("crypto");
    let csv_path = data_dir.join(format!("{}_{}_{}d_bt.csv", symbol, interval, days));

    // Also check in the script's working directory
    let alt_path = PathBuf::from(format!("data/crypto/{}_{}_{}d_bt.csv", symbol, interval, days));

    let final_path = if csv_path.exists() {
        csv_path
    } else if alt_path.exists() {
        // Move to our data dir
        std::fs::copy(&alt_path, &csv_path).ok();
        csv_path
    } else {
        // Try to find it from stdout
        find_csv_from_output(&stdout, symbol, interval, days)?
    };

    read_csv_info(&final_path.to_string_lossy(), symbol)
}

/// Fetch futures OHLCV data via futures_fetcher.py (InsightSentry/RapidAPI)
pub async fn fetch_futures(
    symbol: &str,
    interval: &str,
    days: u32,
    api_key: &str,
) -> Result<FetchResult, String> {
    let script = get_fetchers_dir().join("futures_fetcher.py");
    if !script.exists() {
        return Err(format!("futures_fetcher.py not found at {:?}", script));
    }

    let python = find_python();
    let symbol_owned = symbol.to_string();
    let interval_owned = interval.to_string();
    let api_key_owned = api_key.to_string();
    let script_str = script.to_string_lossy().to_string();

    let output = tokio::task::spawn_blocking(move || {
        Command::new(&python)
            .arg(&script_str)
            .arg(&symbol_owned)
            .arg(&interval_owned)
            .arg(days.to_string())
            .arg("export")
            .env("RAPIDAPI_KEY", &api_key_owned)
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .output()
    })
    .await
    .map_err(|e| format!("Task failed: {}", e))?
    .map_err(|e| format!("Failed to run futures_fetcher.py: {}", e))?;

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    if !output.status.success() {
        return Err(format!("futures_fetcher.py failed:\n{}\n{}", stdout, stderr));
    }

    let data_dir = get_data_dir("futures");
    let csv_path = data_dir.join(format!("{}_{}_{}d_bt.csv", symbol, interval, days));
    let alt_path = PathBuf::from(format!("data/futures/{}_{}_{}d_bt.csv", symbol, interval, days));

    let final_path = if csv_path.exists() {
        csv_path
    } else if alt_path.exists() {
        std::fs::copy(&alt_path, &csv_path).ok();
        csv_path
    } else {
        find_csv_from_output(&stdout, symbol, interval, days)?
    };

    read_csv_info(&final_path.to_string_lossy(), symbol)
}

/// Fetch stock/ETF OHLCV data via alpaca_fetcher.py
pub async fn fetch_stocks(
    symbol: &str,
    interval: &str,
    days: u32,
    api_key: &str,
    api_secret: &str,
) -> Result<FetchResult, String> {
    let script = get_fetchers_dir().join("alpaca_fetcher.py");
    if !script.exists() {
        return Err(format!("alpaca_fetcher.py not found at {:?}", script));
    }

    let python = find_python();
    let symbol_owned = symbol.to_string();
    let interval_owned = interval.to_string();
    let api_key_owned = api_key.to_string();
    let api_secret_owned = api_secret.to_string();
    let script_str = script.to_string_lossy().to_string();

    let output = tokio::task::spawn_blocking(move || {
        Command::new(&python)
            .arg(&script_str)
            .arg(&symbol_owned)
            .arg(&interval_owned)
            .arg(days.to_string())
            .arg("export")
            .env("ALPACA_KEY", &api_key_owned)
            .env("ALPACA_SECRET", &api_secret_owned)
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .output()
    })
    .await
    .map_err(|e| format!("Task failed: {}", e))?
    .map_err(|e| format!("Failed to run alpaca_fetcher.py: {}", e))?;

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();

    if !output.status.success() {
        return Err(format!("alpaca_fetcher.py failed:\n{}\n{}", stdout, stderr));
    }

    let data_dir = get_data_dir("stocks");
    let csv_path = data_dir.join(format!("{}_{}_{}d_bt.csv", symbol, interval, days));
    let alt_path = PathBuf::from(format!("data/stocks/{}_{}_{}d_bt.csv", symbol, interval, days));

    let final_path = if csv_path.exists() {
        csv_path
    } else if alt_path.exists() {
        std::fs::copy(&alt_path, &csv_path).ok();
        csv_path
    } else {
        find_csv_from_output(&stdout, symbol, interval, days)?
    };

    read_csv_info(&final_path.to_string_lossy(), symbol)
}

/// Parse CSV path from Python script output (looks for "Exported ... → path" or "Exported ... -> path")
fn find_csv_from_output(stdout: &str, symbol: &str, interval: &str, days: u32) -> Result<PathBuf, String> {
    // Look for "Exported N bars → /path/file.csv" or "→" or "->"
    for line in stdout.lines() {
        if line.contains("Exported") && (line.contains("→") || line.contains("->")) {
            let path_str = line
                .split('→').last()
                .or_else(|| line.split("->").last())
                .map(|s| s.trim())
                .unwrap_or("");
            if !path_str.is_empty() {
                let p = PathBuf::from(path_str);
                if p.exists() {
                    return Ok(p);
                }
            }
        }
    }
    Err(format!(
        "Could not find exported CSV for {} {} {}d. Script output:\n{}",
        symbol, interval, days, stdout
    ))
}

/// Read a backtester CSV and return summary info
fn read_csv_info(csv_path: &str, symbol: &str) -> Result<FetchResult, String> {
    let content = std::fs::read_to_string(csv_path)
        .map_err(|e| format!("Cannot read CSV {}: {}", csv_path, e))?;

    let lines: Vec<&str> = content.lines().filter(|l| !l.is_empty()).collect();
    let has_header = lines.first().map(|l| l.contains("timestamp")).unwrap_or(false);
    let data_start = if has_header { 1 } else { 0 };
    let bar_count = lines.len() - data_start;

    if bar_count == 0 {
        return Err("CSV file is empty".to_string());
    }

    let first_ts = parse_timestamp(lines[data_start]);
    let last_ts = parse_timestamp(lines[lines.len() - 1]);

    Ok(FetchResult {
        bars: bar_count,
        csv_path: csv_path.to_string(),
        first_date: first_ts,
        last_date: last_ts,
        symbol: symbol.to_string(),
    })
}

fn parse_timestamp(line: &str) -> String {
    let ts_str = line.split(',').next().unwrap_or("0");
    if let Ok(ts_ms) = ts_str.trim().parse::<i64>() {
        chrono::DateTime::from_timestamp_millis(ts_ms)
            .map(|dt| dt.format("%Y-%m-%d %H:%M").to_string())
            .unwrap_or_else(|| ts_str.to_string())
    } else {
        ts_str.to_string()
    }
}
