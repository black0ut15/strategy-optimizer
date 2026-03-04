use crate::backtester::{self, PrecomputedData};
use crate::types::{BacktestResult, Bar, StrategyParams};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::atomic::Ordering;

/// Sweep configuration loaded from JSON
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SweepConfig {
    pub parameters: HashMap<String, HashMap<String, serde_json::Value>>,
    pub sweep_settings: SweepSettings,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SweepSettings {
    pub fee_pct: f64,
    pub max_dd_pct: f64,
    pub min_trades: u32,
    pub top_n: usize,
    pub sort_by: String,
    pub output: String,
}

/// Single result row with all params + metrics
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SweepResult {
    pub params: StrategyParams,
    pub result: BacktestResult,
}

/// Expand a JSON value into a vector of concrete values
fn expand_f64(val: &serde_json::Value) -> Vec<f64> {
    match val {
        serde_json::Value::Array(arr) => {
            if arr.len() == 1 {
                vec![arr[0].as_f64().unwrap_or(0.0)]
            } else if arr.len() == 3 {
                let start = arr[0].as_f64().unwrap_or(0.0);
                let stop = arr[1].as_f64().unwrap_or(0.0);
                let step = arr[2].as_f64().unwrap_or(1.0);
                let mut vals = Vec::new();
                let mut v = start;
                while v <= stop + step * 0.001 {
                    vals.push((v * 100000.0).round() / 100000.0);
                    v += step;
                }
                if vals.is_empty() {
                    vals.push(start);
                }
                vals
            } else {
                arr.iter().map(|v| v.as_f64().unwrap_or(0.0)).collect()
            }
        }
        serde_json::Value::Number(n) => vec![n.as_f64().unwrap_or(0.0)],
        _ => vec![0.0],
    }
}

fn expand_usize(val: &serde_json::Value) -> Vec<usize> {
    expand_f64(val).into_iter().map(|v| v as usize).collect()
}

fn expand_bool(val: &serde_json::Value) -> Vec<bool> {
    match val {
        serde_json::Value::Array(arr) => arr
            .iter()
            .map(|v| v.as_bool().unwrap_or(false))
            .collect(),
        serde_json::Value::Bool(b) => vec![*b],
        _ => vec![false],
    }
}

fn expand_string(val: &serde_json::Value) -> Vec<String> {
    match val {
        serde_json::Value::Array(arr) => arr
            .iter()
            .map(|v| v.as_str().unwrap_or("").to_string())
            .collect(),
        serde_json::Value::String(s) => vec![s.clone()],
        _ => vec![String::new()],
    }
}

fn get_param_vals<'a>(config: &'a SweepConfig, group: &str, key: &str) -> Option<&'a serde_json::Value> {
    config.parameters.get(group)?.get(key)
}

/// Run the full parameter sweep
pub fn run_sweep(
    bars: &[Bar],
    config: &SweepConfig,
    progress_callback: impl Fn(f64) + Send + Sync,
) -> Result<Vec<SweepResult>, String> {
    let precomputed = PrecomputedData::compute(bars);
    let fee_pct = config.sweep_settings.fee_pct;
    let max_dd = config.sweep_settings.max_dd_pct;

    // Build parameter grid from config
    // Look up each parameter from the flat parameters map
    let fast_limits = get_param_vals(config, "MESA Settings", "fastLimit")
        .map(expand_f64)
        .unwrap_or_else(|| vec![0.38]);
    let slow_limits = get_param_vals(config, "MESA Settings", "slowLimit")
        .map(expand_f64)
        .unwrap_or_else(|| vec![0.035]);
    let sep_thresholds = get_param_vals(config, "MESA Settings", "sepThreshold")
        .map(expand_f64)
        .unwrap_or_else(|| vec![0.003]);
    let regime_ema_lens = get_param_vals(config, "Regime Filter", "regimeEmaLen")
        .map(expand_usize)
        .unwrap_or_else(|| vec![200]);
    let use_regime_filters = get_param_vals(config, "Regime Filter", "useRegimeFilter")
        .map(expand_bool)
        .unwrap_or_else(|| vec![true]);
    let regime_modes = get_param_vals(config, "Regime Filter", "regimeMode")
        .map(expand_string)
        .unwrap_or_else(|| vec!["Suppress counter-trend".to_string()]);
    let trade_directions = get_param_vals(config, "Trade Logic", "dirMode")
        .map(expand_string)
        .unwrap_or_else(|| vec!["Both".to_string()]);
    let min_hold_bars_v = get_param_vals(config, "Exit Management", "minHoldBars")
        .map(expand_usize)
        .unwrap_or_else(|| vec![144]);
    let be_trigger_pcts = get_param_vals(config, "Exit Management", "beTriggerPct")
        .map(expand_f64)
        .unwrap_or_else(|| vec![0.30]);
    let be_offset_pcts = get_param_vals(config, "Exit Management", "beOffsetPct")
        .map(expand_f64)
        .unwrap_or_else(|| vec![0.02]);
    let mama_trail_min_pcts = get_param_vals(config, "Exit Management", "mamaTrailMinPct")
        .map(expand_f64)
        .unwrap_or_else(|| vec![0.15]);
    let lock_bars_v = get_param_vals(config, "Exit Management", "lockBars")
        .map(expand_usize)
        .unwrap_or_else(|| vec![48]);
    let lock_min_pcts = get_param_vals(config, "Exit Management", "lockMinPct")
        .map(expand_f64)
        .unwrap_or_else(|| vec![0.15]);
    let lock_retrace_pcts = get_param_vals(config, "Exit Management", "lockRetracePct")
        .map(expand_f64)
        .unwrap_or_else(|| vec![50.0]);
    let reentry_min_bars_v = get_param_vals(config, "Entry Filter", "reentryMinBars")
        .map(expand_usize)
        .unwrap_or_else(|| vec![6]);

    // Toggles
    let use_be_stops = get_param_vals(config, "Exit Management", "useBEStop")
        .map(expand_bool)
        .unwrap_or_else(|| vec![true]);
    let use_mama_trails = get_param_vals(config, "Exit Management", "useMamaTrail")
        .map(expand_bool)
        .unwrap_or_else(|| vec![true]);
    let use_profit_locks = get_param_vals(config, "Exit Management", "useProfitLock")
        .map(expand_bool)
        .unwrap_or_else(|| vec![true]);
    let use_min_holds = get_param_vals(config, "Exit Management", "useMinHold")
        .map(expand_bool)
        .unwrap_or_else(|| vec![true]);

    // Calculate total combos
    let dims: Vec<usize> = vec![
        fast_limits.len(), slow_limits.len(), sep_thresholds.len(),
        regime_ema_lens.len(), use_regime_filters.len(), regime_modes.len(),
        trade_directions.len(),
        min_hold_bars_v.len(), be_trigger_pcts.len(), be_offset_pcts.len(),
        mama_trail_min_pcts.len(), lock_bars_v.len(), lock_min_pcts.len(),
        lock_retrace_pcts.len(), reentry_min_bars_v.len(),
        use_be_stops.len(), use_mama_trails.len(), use_profit_locks.len(), use_min_holds.len(),
    ];
    let total: usize = dims.iter().product();

    println!("=== Strategy Optimizer Sweep ===");
    println!("Total combinations: {}", total);
    println!("Bars: {}", bars.len());

    let counter = std::sync::atomic::AtomicUsize::new(0);

    // Parallel sweep
    let mut results: Vec<SweepResult> = (0..total)
        .into_par_iter()
        .filter_map(|idx| {
            // Unpack index into parameter indices
            let mut rem = idx;
            let mut dim_idx = Vec::with_capacity(dims.len());
            for &d in dims.iter().rev() {
                dim_idx.push(rem % d);
                rem /= d;
            }
            dim_idx.reverse();

            let params = StrategyParams {
                fast_limit: fast_limits[dim_idx[0]],
                slow_limit: slow_limits[dim_idx[1]],
                sep_threshold: sep_thresholds[dim_idx[2]],
                regime_ema_len: regime_ema_lens[dim_idx[3]],
                use_regime_filter: use_regime_filters[dim_idx[4]],
                regime_mode: regime_modes[dim_idx[5]].clone(),
                trade_direction: trade_directions[dim_idx[6]].clone(),
                min_hold_bars: min_hold_bars_v[dim_idx[7]],
                be_trigger_pct: be_trigger_pcts[dim_idx[8]],
                be_offset_pct: be_offset_pcts[dim_idx[9]],
                mama_trail_min_pct: mama_trail_min_pcts[dim_idx[10]],
                lock_bars: lock_bars_v[dim_idx[11]],
                lock_min_pct: lock_min_pcts[dim_idx[12]],
                lock_retrace_pct: lock_retrace_pcts[dim_idx[13]],
                reentry_min_bars: reentry_min_bars_v[dim_idx[14]],
                use_be_stop: use_be_stops[dim_idx[15]],
                use_mama_trail: use_mama_trails[dim_idx[16]],
                use_profit_lock: use_profit_locks[dim_idx[17]],
                use_min_hold: use_min_holds[dim_idx[18]],
                fee_pct,
                max_drawdown_pct: max_dd,
                // Defaults for non-swept params
                use_atr_filter: true,
                atr_min_pct: 0.07,
                margin_pct: 0.10,
                leverage: 5.0,
                min_qty: 0.001,
            };

            let result = backtester::run_backtest(bars, &precomputed, &params);

            // Progress reporting
            let done = counter.fetch_add(1, std::sync::atomic::Ordering::Relaxed) + 1;
            if done % 1000 == 0 || done == total {
                progress_callback(done as f64 / total as f64);
            }

            // Filter
            if result.total_trades < config.sweep_settings.min_trades {
                return None;
            }

            Some(SweepResult { params, result })
        })
        .collect();

    // Sort
    match config.sweep_settings.sort_by.as_str() {
        "net" => results.sort_by(|a, b| b.result.net_profit.partial_cmp(&a.result.net_profit).unwrap()),
        "calmar" => results.sort_by(|a, b| {
            let ca = if a.result.max_drawdown > 0.0 { a.result.net_profit / a.result.max_drawdown } else { 0.0 };
            let cb = if b.result.max_drawdown > 0.0 { b.result.net_profit / b.result.max_drawdown } else { 0.0 };
            cb.partial_cmp(&ca).unwrap()
        }),
        _ => results.sort_by(|a, b| b.result.profit_factor.partial_cmp(&a.result.profit_factor).unwrap()),
    }

    results.truncate(config.sweep_settings.top_n);
    Ok(results)
}

/// Run a chunk of the parameter sweep (for distributed workers).
/// Only processes combos from chunk_start..chunk_end out of the total space.
pub fn run_sweep_chunk(
    bars: &[Bar],
    config: &SweepConfig,
    chunk_start: usize,
    chunk_end: usize,
    progress_callback: impl Fn(usize, usize) + Send + Sync,
    should_stop: impl Fn() -> bool + Send + Sync,
) -> Vec<SweepResult> {
    let precomputed = PrecomputedData::compute(bars);
    let fee_pct = config.sweep_settings.fee_pct;
    let max_dd = config.sweep_settings.max_dd_pct;

    // Build same parameter grid as run_sweep
    let fast_limits = get_param_vals(config, "MESA Settings", "fastLimit")
        .map(expand_f64).unwrap_or_else(|| vec![0.38]);
    let slow_limits = get_param_vals(config, "MESA Settings", "slowLimit")
        .map(expand_f64).unwrap_or_else(|| vec![0.035]);
    let sep_thresholds = get_param_vals(config, "MESA Settings", "sepThreshold")
        .map(expand_f64).unwrap_or_else(|| vec![0.003]);
    let regime_ema_lens = get_param_vals(config, "Regime Filter", "regimeEmaLen")
        .map(expand_usize).unwrap_or_else(|| vec![200]);
    let use_regime_filters = get_param_vals(config, "Regime Filter", "useRegimeFilter")
        .map(expand_bool).unwrap_or_else(|| vec![true]);
    let regime_modes = get_param_vals(config, "Regime Filter", "regimeMode")
        .map(expand_string).unwrap_or_else(|| vec!["Suppress counter-trend".to_string()]);
    let trade_directions = get_param_vals(config, "Trade Logic", "dirMode")
        .map(expand_string).unwrap_or_else(|| vec!["Both".to_string()]);
    let min_hold_bars_v = get_param_vals(config, "Exit Management", "minHoldBars")
        .map(expand_usize).unwrap_or_else(|| vec![144]);
    let be_trigger_pcts = get_param_vals(config, "Exit Management", "beTriggerPct")
        .map(expand_f64).unwrap_or_else(|| vec![0.30]);
    let be_offset_pcts = get_param_vals(config, "Exit Management", "beOffsetPct")
        .map(expand_f64).unwrap_or_else(|| vec![0.02]);
    let mama_trail_min_pcts = get_param_vals(config, "Exit Management", "mamaTrailMinPct")
        .map(expand_f64).unwrap_or_else(|| vec![0.15]);
    let lock_bars_v = get_param_vals(config, "Exit Management", "lockBars")
        .map(expand_usize).unwrap_or_else(|| vec![48]);
    let lock_min_pcts = get_param_vals(config, "Exit Management", "lockMinPct")
        .map(expand_f64).unwrap_or_else(|| vec![0.15]);
    let lock_retrace_pcts = get_param_vals(config, "Exit Management", "lockRetracePct")
        .map(expand_f64).unwrap_or_else(|| vec![50.0]);
    let reentry_min_bars_v = get_param_vals(config, "Entry Filter", "reentryMinBars")
        .map(expand_usize).unwrap_or_else(|| vec![6]);
    let use_be_stops = get_param_vals(config, "Exit Management", "useBEStop")
        .map(expand_bool).unwrap_or_else(|| vec![true]);
    let use_mama_trails = get_param_vals(config, "Exit Management", "useMamaTrail")
        .map(expand_bool).unwrap_or_else(|| vec![true]);
    let use_profit_locks = get_param_vals(config, "Exit Management", "useProfitLock")
        .map(expand_bool).unwrap_or_else(|| vec![true]);
    let use_min_holds = get_param_vals(config, "Exit Management", "useMinHold")
        .map(expand_bool).unwrap_or_else(|| vec![true]);

    let dims: Vec<usize> = vec![
        fast_limits.len(), slow_limits.len(), sep_thresholds.len(),
        regime_ema_lens.len(), use_regime_filters.len(), regime_modes.len(),
        trade_directions.len(),
        min_hold_bars_v.len(), be_trigger_pcts.len(), be_offset_pcts.len(),
        mama_trail_min_pcts.len(), lock_bars_v.len(), lock_min_pcts.len(),
        lock_retrace_pcts.len(), reentry_min_bars_v.len(),
        use_be_stops.len(), use_mama_trails.len(), use_profit_locks.len(), use_min_holds.len(),
    ];

    let chunk_size = chunk_end - chunk_start;
    let counter = std::sync::atomic::AtomicUsize::new(0);

    let mut results: Vec<SweepResult> = (chunk_start..chunk_end)
        .into_par_iter()
        .filter_map(|idx| {
            if should_stop() {
                return None;
            }

            let mut rem = idx;
            let mut dim_idx = Vec::with_capacity(dims.len());
            for &d in dims.iter().rev() {
                dim_idx.push(rem % d);
                rem /= d;
            }
            dim_idx.reverse();

            let params = StrategyParams {
                fast_limit: fast_limits[dim_idx[0]],
                slow_limit: slow_limits[dim_idx[1]],
                sep_threshold: sep_thresholds[dim_idx[2]],
                regime_ema_len: regime_ema_lens[dim_idx[3]],
                use_regime_filter: use_regime_filters[dim_idx[4]],
                regime_mode: regime_modes[dim_idx[5]].clone(),
                trade_direction: trade_directions[dim_idx[6]].clone(),
                min_hold_bars: min_hold_bars_v[dim_idx[7]],
                be_trigger_pct: be_trigger_pcts[dim_idx[8]],
                be_offset_pct: be_offset_pcts[dim_idx[9]],
                mama_trail_min_pct: mama_trail_min_pcts[dim_idx[10]],
                lock_bars: lock_bars_v[dim_idx[11]],
                lock_min_pct: lock_min_pcts[dim_idx[12]],
                lock_retrace_pct: lock_retrace_pcts[dim_idx[13]],
                reentry_min_bars: reentry_min_bars_v[dim_idx[14]],
                use_be_stop: use_be_stops[dim_idx[15]],
                use_mama_trail: use_mama_trails[dim_idx[16]],
                use_profit_lock: use_profit_locks[dim_idx[17]],
                use_min_hold: use_min_holds[dim_idx[18]],
                fee_pct,
                max_drawdown_pct: max_dd,
                use_atr_filter: true,
                atr_min_pct: 0.07,
                margin_pct: 0.10,
                leverage: 5.0,
                min_qty: 0.001,
            };

            let result = backtester::run_backtest(bars, &precomputed, &params);

            let done = counter.fetch_add(1, Ordering::Relaxed) + 1;
            if done % 500 == 0 || done == chunk_size {
                progress_callback(done, chunk_size);
            }

            if result.total_trades < config.sweep_settings.min_trades {
                return None;
            }

            Some(SweepResult { params, result })
        })
        .collect();

    // Sort by configured metric
    match config.sweep_settings.sort_by.as_str() {
        "net" => results.sort_by(|a, b| b.result.net_profit.partial_cmp(&a.result.net_profit).unwrap()),
        "calmar" => results.sort_by(|a, b| {
            let ca = if a.result.max_drawdown > 0.0 { a.result.net_profit / a.result.max_drawdown } else { 0.0 };
            let cb = if b.result.max_drawdown > 0.0 { b.result.net_profit / b.result.max_drawdown } else { 0.0 };
            cb.partial_cmp(&ca).unwrap()
        }),
        _ => results.sort_by(|a, b| b.result.profit_factor.partial_cmp(&a.result.profit_factor).unwrap()),
    }

    results.truncate(config.sweep_settings.top_n);
    results
}
