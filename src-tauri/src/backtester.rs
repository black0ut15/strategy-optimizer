use crate::mesa::MesaState;
use crate::types::{BacktestResult, Bar, StrategyParams};

/// Precomputed data that's parameter-independent
pub struct PrecomputedData {
    pub atr_14: Vec<f64>,
}

impl PrecomputedData {
    pub fn compute(bars: &[Bar]) -> Self {
        let n = bars.len();
        let mut atr = vec![0.0f64; n];

        for i in 0..n {
            let tr = if i == 0 {
                bars[i].high - bars[i].low
            } else {
                let pc = bars[i - 1].close;
                (bars[i].high - bars[i].low)
                    .max((bars[i].high - pc).abs())
                    .max((bars[i].low - pc).abs())
            };

            if i < 14 {
                let sum: f64 = (0..=i)
                    .map(|j| {
                        if j == 0 {
                            bars[j].high - bars[j].low
                        } else {
                            let pc = bars[j - 1].close;
                            (bars[j].high - bars[j].low)
                                .max((bars[j].high - pc).abs())
                                .max((bars[j].low - pc).abs())
                        }
                    })
                    .sum();
                atr[i] = sum / (i + 1) as f64;
            } else {
                atr[i] = (atr[i - 1] * 13.0 + tr) / 14.0;
            }
        }

        Self { atr_14: atr }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum Position {
    Flat,
    Long,
    Short,
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum ExitReason {
    None,
    BeStop,
    MamaTrail,
    ProfitLock,
    CrossExit,
}

/// Run a single backtest for one parameter combination.
/// 
/// NOTE: Copy your validated, parity-proven strategy.rs `run_backtest` 
/// function here. The version below is the scaffold with regime_mode 
/// and trade_direction support added. Replace the MESA computation 
/// section with your exact parity code from the standalone backtester.
pub fn run_backtest(
    bars: &[Bar],
    precomputed: &PrecomputedData,
    params: &StrategyParams,
) -> BacktestResult {
    let n = bars.len();
    if n < 210 {
        return BacktestResult::empty();
    }

    let mut mesa = MesaState::new();
    let mut position = Position::Flat;
    let mut entry_price: f64 = 0.0;
    let mut entry_bar: usize = 0;
    let mut be_stop_active = false;
    let mut trade_max_pct: f64 = 0.0;
    let mut last_exit_bar: usize = 0;
    let mut last_exit_dir: i8 = 0;
    let mut pending_long = false;
    let mut pending_short = false;

    let mut equity: f64 = 1000.0;
    let mut peak_equity: f64 = 1000.0;
    let mut max_drawdown: f64 = 0.0;
    let mut total_trades: u32 = 0;
    let mut winning_trades: u32 = 0;
    let mut gross_profit: f64 = 0.0;
    let mut gross_loss: f64 = 0.0;
    let mut total_fees: f64 = 0.0;

    let mut regime_ema: f64 = 0.0;
    let ema_mult = 2.0 / (params.regime_ema_len as f64 + 1.0);

    for i in 0..n {
        let bar = &bars[i];
        let close = bar.close;
        let src = bar.hl2();

        mesa.update(src, params.fast_limit, params.slow_limit);

        if i == 0 {
            regime_ema = close;
        } else {
            regime_ema = close * ema_mult + regime_ema * (1.0 - ema_mult);
        }

        if i < params.regime_ema_len.max(10) {
            continue;
        }

        let mama = mesa.mama;
        let fama = mesa.fama;
        let mama_cross_up = mesa.cross_up();
        let mama_cross_down = mesa.cross_down();

        if mama_cross_up {
            pending_long = true;
            pending_short = false;
        }
        if mama_cross_down {
            pending_short = true;
            pending_long = false;
        }
        if pending_long && mama < fama {
            pending_long = false;
        }
        if pending_short && mama > fama {
            pending_short = false;
        }

        let diff_ok = if params.sep_threshold <= 0.0 {
            true
        } else {
            (mama - fama).abs() / close > params.sep_threshold
        };

        let bull_regime = close > regime_ema;
        let bear_regime = close < regime_ema;

        // Trade direction filter
        let mut want_long = params.trade_direction != "Short only";
        let mut want_short = params.trade_direction != "Long only";

        // Regime filter with mode support
        if params.use_regime_filter {
            if params.regime_mode == "Suppress counter-trend" {
                if bear_regime {
                    want_long = false;
                }
                if bull_regime {
                    want_short = false;
                }
            } else {
                // "Trend-aligned only"
                want_long = want_long && bull_regime;
                want_short = want_short && bear_regime;
            }
        }

        let long_signal_changed = pending_long && diff_ok && want_long;
        let short_signal_changed = pending_short && diff_ok && want_short;

        // --- Position management ---
        let bars_held = if position != Position::Flat {
            i.saturating_sub(entry_bar)
        } else {
            0
        };
        let hold_time_met = !params.use_min_hold || bars_held >= params.min_hold_bars;

        let unreal_pct = match position {
            Position::Long => (close - entry_price) / entry_price * 100.0,
            Position::Short => (entry_price - close) / entry_price * 100.0,
            Position::Flat => 0.0,
        };

        if unreal_pct > trade_max_pct {
            trade_max_pct = unreal_pct;
        }

        if params.use_be_stop && !be_stop_active && trade_max_pct >= params.be_trigger_pct {
            be_stop_active = true;
        }

        // --- Exit signals ---
        let mut exit_signal = false;
        let mut exit_reason = ExitReason::None;
        let mut be_stop_hit = false;

        if position != Position::Flat {
            if params.use_be_stop && be_stop_active {
                let be_price = match position {
                    Position::Long => entry_price * (1.0 + params.be_offset_pct / 100.0),
                    Position::Short => entry_price * (1.0 - params.be_offset_pct / 100.0),
                    _ => 0.0,
                };
                let hit = match position {
                    Position::Long => close <= be_price,
                    Position::Short => close >= be_price,
                    _ => false,
                };
                if hit {
                    exit_signal = true;
                    exit_reason = ExitReason::BeStop;
                    be_stop_hit = true;
                }
            }

            if !exit_signal && params.use_mama_trail && hold_time_met && unreal_pct >= params.mama_trail_min_pct {
                let trail_hit = match position {
                    Position::Long => close < mama,
                    Position::Short => close > mama,
                    _ => false,
                };
                if trail_hit {
                    exit_signal = true;
                    exit_reason = ExitReason::MamaTrail;
                }
            }

            if !exit_signal && params.use_profit_lock && bars_held >= params.lock_bars && trade_max_pct >= params.lock_min_pct {
                let lock_floor = trade_max_pct * (params.lock_retrace_pct / 100.0);
                if unreal_pct < lock_floor {
                    exit_signal = true;
                    exit_reason = ExitReason::ProfitLock;
                }
            }

            if !exit_signal && hold_time_met {
                let cross_exit = match position {
                    Position::Long => mama_cross_down || (mama < fama && diff_ok),
                    Position::Short => mama_cross_up || (mama > fama && diff_ok),
                    _ => false,
                };
                if cross_exit {
                    exit_signal = true;
                    exit_reason = ExitReason::CrossExit;
                }
            }

            if !exit_signal && hold_time_met {
                let expired = match position {
                    Position::Long => mama < fama,
                    Position::Short => mama > fama,
                    _ => false,
                };
                if expired {
                    exit_signal = true;
                    exit_reason = ExitReason::CrossExit;
                }
            }
        }

        // --- Trade execution ---
        let can_reverse_long = hold_time_met && short_signal_changed && want_short;
        let can_reverse_short = hold_time_met && long_signal_changed && want_long;
        let mut acted = false;

        if position == Position::Long && can_reverse_long && !be_stop_hit {
            let pnl = close_position(position, entry_price, close, params.fee_pct, params.leverage);
            apply_pnl(pnl, &mut equity, &mut total_trades, &mut winning_trades, &mut gross_profit, &mut gross_loss, &mut total_fees, params.fee_pct, params.leverage);
            position = Position::Short;
            entry_price = close;
            entry_bar = i;
            be_stop_active = false;
            trade_max_pct = 0.0;
            pending_short = false;
            acted = true;
        } else if position == Position::Short && can_reverse_short && !be_stop_hit {
            let pnl = close_position(position, entry_price, close, params.fee_pct, params.leverage);
            apply_pnl(pnl, &mut equity, &mut total_trades, &mut winning_trades, &mut gross_profit, &mut gross_loss, &mut total_fees, params.fee_pct, params.leverage);
            position = Position::Long;
            entry_price = close;
            entry_bar = i;
            be_stop_active = false;
            trade_max_pct = 0.0;
            pending_long = false;
            acted = true;
        } else if position != Position::Flat && exit_signal && !acted {
            let can_rev_on_exit = match (&exit_reason, position) {
                (ExitReason::CrossExit, Position::Long) => mama < fama && want_short,
                (ExitReason::CrossExit, Position::Short) => mama > fama && want_long,
                _ => false,
            };

            let pnl = close_position(position, entry_price, close, params.fee_pct, params.leverage);
            apply_pnl(pnl, &mut equity, &mut total_trades, &mut winning_trades, &mut gross_profit, &mut gross_loss, &mut total_fees, params.fee_pct, params.leverage);

            if can_rev_on_exit {
                position = match position {
                    Position::Long => Position::Short,
                    Position::Short => Position::Long,
                    _ => Position::Flat,
                };
                entry_price = close;
                entry_bar = i;
                be_stop_active = false;
                trade_max_pct = 0.0;
            } else {
                last_exit_bar = i;
                last_exit_dir = match position {
                    Position::Long => 1,
                    Position::Short => -1,
                    _ => 0,
                };
                position = Position::Flat;
                entry_price = 0.0;
                be_stop_active = false;
                trade_max_pct = 0.0;
            }
            acted = true;
        }

        if !acted && position == Position::Flat {
            let long_cooldown = last_exit_dir == 1 && i.saturating_sub(last_exit_bar) < params.reentry_min_bars;
            let short_cooldown = last_exit_dir == -1 && i.saturating_sub(last_exit_bar) < params.reentry_min_bars;

            if long_signal_changed && !long_cooldown {
                position = Position::Long;
                entry_price = close;
                entry_bar = i;
                be_stop_active = false;
                trade_max_pct = 0.0;
                pending_long = false;
            } else if short_signal_changed && !short_cooldown {
                position = Position::Short;
                entry_price = close;
                entry_bar = i;
                be_stop_active = false;
                trade_max_pct = 0.0;
                pending_short = false;
            }
        }

        if equity > peak_equity {
            peak_equity = equity;
        }
        let dd = (peak_equity - equity) / peak_equity * 100.0;
        if dd > max_drawdown {
            max_drawdown = dd;
        }
        if params.max_drawdown_pct > 0.0 && dd > params.max_drawdown_pct {
            return BacktestResult {
                total_trades, winning_trades, gross_profit, gross_loss,
                net_profit: equity - 1000.0, max_drawdown,
                profit_factor: if gross_loss.abs() > 0.0 { gross_profit / gross_loss.abs() } else { f64::INFINITY },
                win_rate: if total_trades > 0 { winning_trades as f64 / total_trades as f64 * 100.0 } else { 0.0 },
                total_fees, early_terminated: true,
            };
        }
    }

    // Close open position at end
    if position != Position::Flat {
        let close = bars.last().unwrap().close;
        let pnl = close_position(position, entry_price, close, params.fee_pct, params.leverage);
        apply_pnl(pnl, &mut equity, &mut total_trades, &mut winning_trades, &mut gross_profit, &mut gross_loss, &mut total_fees, params.fee_pct, params.leverage);
    }

    BacktestResult {
        total_trades, winning_trades, gross_profit, gross_loss,
        net_profit: equity - 1000.0, max_drawdown,
        profit_factor: if gross_loss.abs() > 0.0 { gross_profit / gross_loss.abs() } else if gross_profit > 0.0 { f64::INFINITY } else { 0.0 },
        win_rate: if total_trades > 0 { winning_trades as f64 / total_trades as f64 * 100.0 } else { 0.0 },
        total_fees, early_terminated: false,
    }
}

#[inline(always)]
fn close_position(pos: Position, entry: f64, exit: f64, fee_pct: f64, leverage: f64) -> f64 {
    let raw_pct = match pos {
        Position::Long => (exit - entry) / entry,
        Position::Short => (entry - exit) / entry,
        Position::Flat => 0.0,
    };
    raw_pct * leverage - fee_pct / 100.0
}

#[inline(always)]
fn apply_pnl(
    pnl_pct: f64, equity: &mut f64, total_trades: &mut u32, winning_trades: &mut u32,
    gross_profit: &mut f64, gross_loss: &mut f64, total_fees: &mut f64,
    fee_pct: f64, leverage: f64,
) {
    let pnl_dollar = *equity * pnl_pct;
    let fee_dollar = *equity * (fee_pct / 100.0);
    *equity += pnl_dollar;
    *total_trades += 1;
    *total_fees += fee_dollar;
    let gross_pnl = pnl_dollar + fee_dollar;
    if gross_pnl > 0.0 {
        *winning_trades += 1;
        *gross_profit += gross_pnl;
    } else {
        *gross_loss += gross_pnl.abs();
    }
}
