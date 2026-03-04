use serde::{Deserialize, Serialize};

/// Single OHLCV bar
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Bar {
    pub timestamp: String,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

impl Bar {
    pub fn hl2(&self) -> f64 {
        (self.high + self.low) / 2.0
    }

    pub fn ohlc4(&self) -> f64 {
        (self.open + self.high + self.low + self.close) / 4.0
    }

    pub fn hlc3(&self) -> f64 {
        (self.high + self.low + self.close) / 3.0
    }

    /// Load bars from CSV (TradingView export format)
    /// Expects columns: time, open, high, low, close, Volume (header row)
    pub fn load_csv(path: &str) -> Result<Vec<Bar>, Box<dyn std::error::Error>> {
        let mut reader = csv::ReaderBuilder::new()
            .has_headers(true)
            .flexible(true)
            .from_path(path)?;

        let mut bars = Vec::new();

        for result in reader.records() {
            let record = result?;
            if record.len() < 5 {
                continue;
            }

            let timestamp = record.get(0).unwrap_or("").to_string();
            let open: f64 = record.get(1).unwrap_or("0").parse().unwrap_or(0.0);
            let high: f64 = record.get(2).unwrap_or("0").parse().unwrap_or(0.0);
            let low: f64 = record.get(3).unwrap_or("0").parse().unwrap_or(0.0);
            let close: f64 = record.get(4).unwrap_or("0").parse().unwrap_or(0.0);
            let volume: f64 = record.get(5).unwrap_or("0").parse().unwrap_or(0.0);

            if open > 0.0 && high > 0.0 && low > 0.0 && close > 0.0 {
                bars.push(Bar {
                    timestamp,
                    open,
                    high,
                    low,
                    close,
                    volume,
                });
            }
        }

        Ok(bars)
    }

    /// Load bars from CSV string (for receiving data over network)
    pub fn load_csv_from_string(data: &str) -> Result<Vec<Bar>, Box<dyn std::error::Error>> {
        let mut reader = csv::ReaderBuilder::new()
            .has_headers(true)
            .flexible(true)
            .from_reader(data.as_bytes());

        let mut bars = Vec::new();

        for result in reader.records() {
            let record = result?;
            if record.len() < 5 {
                continue;
            }

            let timestamp = record.get(0).unwrap_or("").to_string();
            let open: f64 = record.get(1).unwrap_or("0").parse().unwrap_or(0.0);
            let high: f64 = record.get(2).unwrap_or("0").parse().unwrap_or(0.0);
            let low: f64 = record.get(3).unwrap_or("0").parse().unwrap_or(0.0);
            let close: f64 = record.get(4).unwrap_or("0").parse().unwrap_or(0.0);
            let volume: f64 = record.get(5).unwrap_or("0").parse().unwrap_or(0.0);

            if open > 0.0 && high > 0.0 && low > 0.0 && close > 0.0 {
                bars.push(Bar { timestamp, open, high, low, close, volume });
            }
        }

        Ok(bars)
    }
}

/// Backtest result for a single parameter combination
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BacktestResult {
    pub total_trades: u32,
    pub winning_trades: u32,
    pub gross_profit: f64,
    pub gross_loss: f64,
    pub net_profit: f64,
    pub max_drawdown: f64,
    pub profit_factor: f64,
    pub win_rate: f64,
    pub total_fees: f64,
    pub early_terminated: bool,
}

impl BacktestResult {
    pub fn empty() -> Self {
        Self {
            total_trades: 0,
            winning_trades: 0,
            gross_profit: 0.0,
            gross_loss: 0.0,
            net_profit: 0.0,
            max_drawdown: 0.0,
            profit_factor: 0.0,
            win_rate: 0.0,
            total_fees: 0.0,
            early_terminated: false,
        }
    }
}

/// Strategy parameters — all tunable values
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StrategyParams {
    // MESA
    pub fast_limit: f64,
    pub slow_limit: f64,
    pub sep_threshold: f64,

    // Regime filter
    pub use_regime_filter: bool,
    pub regime_ema_len: usize,
    pub regime_mode: String, // "Suppress counter-trend" or "Trend-aligned only"

    // Trade direction
    pub trade_direction: String, // "Both", "Long only", "Short only"

    // Exit management
    pub use_be_stop: bool,
    pub be_trigger_pct: f64,
    pub be_offset_pct: f64,
    pub use_mama_trail: bool,
    pub mama_trail_min_pct: f64,
    pub use_profit_lock: bool,
    pub lock_bars: usize,
    pub lock_min_pct: f64,
    pub lock_retrace_pct: f64,
    pub use_min_hold: bool,
    pub min_hold_bars: usize,

    // Entry filter
    pub reentry_min_bars: usize,

    // ATR filter
    pub use_atr_filter: bool,
    pub atr_min_pct: f64,

    // Sizing
    pub margin_pct: f64,
    pub leverage: f64,
    pub min_qty: f64,

    // Fees
    pub fee_pct: f64,

    // Early termination
    pub max_drawdown_pct: f64,
}

impl Default for StrategyParams {
    fn default() -> Self {
        Self {
            fast_limit: 0.38,
            slow_limit: 0.035,
            sep_threshold: 0.003,
            use_regime_filter: true,
            regime_ema_len: 200,
            regime_mode: "Suppress counter-trend".to_string(),
            trade_direction: "Both".to_string(),
            use_be_stop: true,
            be_trigger_pct: 0.30,
            be_offset_pct: 0.02,
            use_mama_trail: true,
            mama_trail_min_pct: 0.15,
            use_profit_lock: true,
            lock_bars: 48,
            lock_min_pct: 0.15,
            lock_retrace_pct: 50.0,
            use_min_hold: true,
            min_hold_bars: 144,
            reentry_min_bars: 6,
            use_atr_filter: true,
            atr_min_pct: 0.07,
            margin_pct: 0.10,
            leverage: 5.0,
            min_qty: 0.001,
            fee_pct: 0.07,
            max_drawdown_pct: 0.0,
        }
    }
}
