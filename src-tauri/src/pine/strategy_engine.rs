/// Pine Script Strategy Engine
/// Handles strategy.entry, strategy.exit, strategy.close, strategy.close_all
/// Tracks positions, pending orders, equity curve, and performance metrics.

use super::vm::{Value, BarData, BacktestResult};

#[derive(Debug, Clone, PartialEq)]
enum Direction {
    Long,
    Short,
    Flat,
}

#[derive(Debug, Clone)]
struct PendingExit {
    from_entry: String,
    stop: Option<f64>,
    limit: Option<f64>,
}

#[derive(Debug, Clone)]
pub struct StrategyEngine {
    // Position state
    direction: Direction,
    entry_price: f64,
    position_qty: f64,
    entry_bar: usize,

    // Pending orders
    pending_exits: Vec<PendingExit>,

    // Account state
    initial_capital: f64,
    equity: f64,
    peak_equity: f64,
    max_drawdown: f64,
    fee_pct: f64,

    // Stats
    total_trades: u32,
    winning_trades: u32,
    gross_profit: f64,
    gross_loss: f64,

    // Config
    pyramiding: u32,
    process_on_close: bool,
}

impl StrategyEngine {
    pub fn new(initial_capital: f64, fee_pct: f64) -> Self {
        Self {
            direction: Direction::Flat,
            entry_price: 0.0,
            position_qty: 1.0,
            entry_bar: 0,
            pending_exits: Vec::new(),
            initial_capital,
            equity: initial_capital,
            peak_equity: initial_capital,
            max_drawdown: 0.0,
            fee_pct,
            total_trades: 0,
            winning_trades: 0,
            gross_profit: 0.0,
            gross_loss: 0.0,
            pyramiding: 0,
            process_on_close: false,
        }
    }

    pub fn position_size(&self) -> f64 {
        match self.direction {
            Direction::Long => self.position_qty,
            Direction::Short => -self.position_qty,
            Direction::Flat => 0.0,
        }
    }

    pub fn avg_entry_price(&self) -> f64 {
        if self.direction == Direction::Flat {
            f64::NAN
        } else {
            self.entry_price
        }
    }

    pub fn equity(&self) -> f64 {
        self.equity
    }

    /// Handle strategy.entry call
    pub fn handle_entry(&mut self, args: &[Value], bar_index: usize) {
        if args.is_empty() { return; }

        let _id = args[0].to_string_val();

        // Direction: strategy.long (1) or strategy.short (-1)
        let dir_val = args.get(1).map(|v| v.to_i64()).unwrap_or(1);
        let new_dir = if dir_val > 0 { Direction::Long } else { Direction::Short };

        // Qty (optional)
        let qty = args.iter().find_map(|a| {
            let f = a.to_f64();
            if f > 0.0 && f < 1e9 { Some(f) } else { None }
        }).unwrap_or(1.0);

        // If we're in the opposite direction, close first
        if self.direction != Direction::Flat && self.direction != new_dir {
            self.close_position(self.entry_price, bar_index); // simplified: use entry for now
            // The actual close price will be set in process_bar
        }

        // Open new position
        if self.direction == Direction::Flat {
            self.direction = new_dir;
            self.position_qty = qty;
            self.entry_bar = bar_index;
            // entry_price set in process_bar (at close of bar)
            self.pending_exits.clear();
        }
    }

    /// Handle strategy.exit call
    pub fn handle_exit(&mut self, args: &[Value], _bar_index: usize) {
        if args.len() < 2 { return; }

        let _id = args[0].to_string_val();
        let from_entry = args.get(1).map(|v| v.to_string_val()).unwrap_or_default();

        // Parse named args — look for stop= and limit=
        let mut stop = None;
        let mut limit = None;

        // Strategy.exit args are typically: (id, from_entry, stop=X, limit=Y)
        // Since we compile named args as sequential values, we need to handle this
        // In the compiled bytecode, args come in the order they appear
        for (_i, arg) in args.iter().enumerate().skip(2) {
            let f = arg.to_f64();
            if !f.is_nan() && f > 0.0 {
                if stop.is_none() {
                    stop = Some(f); // First numeric is stop
                } else if limit.is_none() {
                    limit = Some(f); // Second numeric is limit (TP)
                }
            }
        }

        self.pending_exits.push(PendingExit {
            from_entry,
            stop,
            limit,
        });
    }

    /// Handle strategy.close call
    pub fn handle_close(&mut self, _args: &[Value], _bar_index: usize) {
        // Close specific entry — for simplicity, close the entire position
        if self.direction != Direction::Flat {
            // Will be closed at bar processing time
            self.pending_exits.push(PendingExit {
                from_entry: String::new(),
                stop: None,
                limit: Some(0.0), // marker: close at market
            });
        }
    }

    /// Handle strategy.close_all
    pub fn close_all(&mut self, _bar_index: usize) {
        self.pending_exits.push(PendingExit {
            from_entry: String::new(),
            stop: None,
            limit: Some(0.0), // marker: close at market
        });
    }

    /// Process bar: fill pending orders, update equity
    pub fn process_bar(&mut self, bar: &BarData, bar_index: usize) {
        let close = bar.close;
        let high = bar.high;
        let low = bar.low;

        // If just entered this bar, set entry price
        if self.direction != Direction::Flat && self.entry_bar == bar_index {
            self.entry_price = close;
        }

        // Check pending exits
        let mut should_close = false;
        let mut close_price = close;

        let exits = self.pending_exits.clone();
        for exit in &exits {
            if exit.stop.is_none() && exit.limit == Some(0.0) {
                // Market close
                should_close = true;
                close_price = close;
                break;
            }

            match self.direction {
                Direction::Long => {
                    // Stop loss: exit if price goes below stop
                    if let Some(stop) = exit.stop {
                        if low <= stop {
                            should_close = true;
                            close_price = stop;
                            break;
                        }
                    }
                    // Take profit: exit if price goes above limit
                    if let Some(limit) = exit.limit {
                        if limit > 0.0 && high >= limit {
                            should_close = true;
                            close_price = limit;
                            break;
                        }
                    }
                }
                Direction::Short => {
                    // Stop loss: exit if price goes above stop
                    if let Some(stop) = exit.stop {
                        if high >= stop {
                            should_close = true;
                            close_price = stop;
                            break;
                        }
                    }
                    // Take profit: exit if price goes below limit
                    if let Some(limit) = exit.limit {
                        if limit > 0.0 && low <= limit {
                            should_close = true;
                            close_price = limit;
                            break;
                        }
                    }
                }
                Direction::Flat => {}
            }
        }

        if should_close && self.direction != Direction::Flat {
            self.close_position(close_price, bar_index);
        }

        // Update equity with unrealized PnL
        if self.direction != Direction::Flat {
            let unreal = match self.direction {
                Direction::Long => (close - self.entry_price) * self.position_qty,
                Direction::Short => (self.entry_price - close) * self.position_qty,
                Direction::Flat => 0.0,
            };
            let current_equity = self.equity + unreal;
            if current_equity > self.peak_equity {
                self.peak_equity = current_equity;
            }
            let dd = self.peak_equity - current_equity;
            if dd > self.max_drawdown {
                self.max_drawdown = dd;
            }
        }
    }

    fn close_position(&mut self, exit_price: f64, _bar_index: usize) {
        if self.direction == Direction::Flat {
            return;
        }

        let pnl = match self.direction {
            Direction::Long => (exit_price - self.entry_price) * self.position_qty,
            Direction::Short => (self.entry_price - exit_price) * self.position_qty,
            Direction::Flat => 0.0,
        };

        // Fees on entry and exit (round trip)
        let notional = exit_price * self.position_qty;
        let fee = notional * self.fee_pct / 100.0 * 2.0; // round trip
        let net_pnl = pnl - fee;

        self.equity += net_pnl;
        self.total_trades += 1;

        if net_pnl > 0.0 {
            self.winning_trades += 1;
            self.gross_profit += net_pnl;
        } else {
            self.gross_loss += net_pnl.abs();
        }

        // Update peak/drawdown
        if self.equity > self.peak_equity {
            self.peak_equity = self.equity;
        }
        let dd = self.peak_equity - self.equity;
        if dd > self.max_drawdown {
            self.max_drawdown = dd;
        }

        // Reset position
        self.direction = Direction::Flat;
        self.entry_price = 0.0;
        self.position_qty = 0.0;
        self.entry_bar = 0;
        self.pending_exits.clear();
    }

    /// Generate final backtest result
    pub fn result(&self) -> BacktestResult {
        let pf = if self.gross_loss > 0.0 {
            self.gross_profit / self.gross_loss
        } else if self.gross_profit > 0.0 {
            999.0
        } else {
            0.0
        };

        let wr = if self.total_trades > 0 {
            self.winning_trades as f64 / self.total_trades as f64 * 100.0
        } else {
            0.0
        };

        BacktestResult {
            profit_factor: pf,
            net_profit: self.equity - self.initial_capital,
            max_drawdown: self.max_drawdown,
            win_rate: wr,
            total_trades: self.total_trades,
            gross_profit: self.gross_profit,
            gross_loss: self.gross_loss,
        }
    }
}
