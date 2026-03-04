"""MESA MAMA FAMA Crypto v3.3 — BTC Optimized.

Hand-translated from Pine Script. This is the reference implementation
for validating the Python backtester against TradingView results.

TradingView baseline (BTCUSDT 5m, 365d, $1M capital, 0.07% RT fees):
  PF ~1.60 | WR ~55.9% | ~598 trades
"""

import numpy as np
from strategy_base import Strategy, Param
from backtest_engine import BarData, Context, Bars
import ta as ta_lib


class MesaMamaFama(Strategy):
    params = {
        # Trade Logic
        "dir_mode": Param("Both", options=["Both", "Long only", "Short only"],
                          group="Trade Logic"),
        "use_swing_logic": Param(True, group="Trade Logic"),

        # MESA Settings
        "fast_limit": Param(0.38, min=0.01, max=1.0, step=0.01,
                            group="MESA Settings"),
        "slow_limit": Param(0.035, min=0.001, max=0.5, step=0.001,
                            group="MESA Settings"),
        "sep_threshold": Param(0.003, min=0.0, max=0.01, step=0.0001,
                               group="MESA Settings"),

        # Exit Management
        "use_be_stop": Param(True, group="Exit Management"),
        "be_trigger_pct": Param(0.30, min=0.05, max=2.0, step=0.01,
                                group="Exit Management"),
        "be_offset_pct": Param(0.02, min=0.0, max=0.1, step=0.01,
                               group="Exit Management"),
        "use_mama_trail": Param(True, group="Exit Management"),
        "mama_trail_min_pct": Param(0.15, min=0.0, max=1.0, step=0.01,
                                    group="Exit Management"),
        "use_profit_lock": Param(True, group="Exit Management"),
        "lock_bars": Param(48, min=0, max=500, step=1,
                           group="Exit Management"),
        "lock_min_pct": Param(0.15, min=0.0, max=1.0, step=0.01,
                              group="Exit Management"),
        "lock_retrace_pct": Param(50.0, min=10.0, max=90.0, step=5.0,
                                  group="Exit Management"),
        "use_min_hold": Param(True, group="Exit Management"),
        "min_hold_bars": Param(144, min=0, max=500, step=1,
                               group="Exit Management"),

        # Regime Filter
        "use_regime_filter": Param(True, group="Regime Filter"),
        "regime_ema_len": Param(200, min=10, max=500, step=10,
                                group="Regime Filter"),
        "regime_mode": Param("Suppress counter-trend",
                             options=["Suppress counter-trend", "Trend-aligned only"],
                             group="Regime Filter"),

        # ATR Filter
        "use_atr_filter": Param(True, group="ATR Filter"),
        "atr_len": Param(14, min=1, max=50, step=1, group="ATR Filter"),
        "min_atr_pct_global": Param(0.10, min=0.01, max=1.0, step=0.01,
                                     group="ATR Filter"),
        "min_atr_pct_btc": Param(0.07, min=0.01, max=0.5, step=0.01,
                                  group="ATR Filter"),

        # Sizing
        "pos_pct": Param(10.0, min=1.0, max=50.0, step=0.1,
                         group="Sizing"),
        "max_leverage": Param(5.0, min=1.0, max=20.0, step=0.5,
                              group="Sizing"),
        "min_leverage": Param(1.0, min=0.5, max=10.0, step=0.5,
                              group="Sizing"),
        "min_qty": Param(0.001, min=0.0001, max=0.1, step=0.0001,
                         group="Sizing"),

        # Anti-churn
        "reentry_min_bars": Param(6, min=0, max=50, step=1,
                                   group="Trade Logic"),
    }

    def init(self, ctx: Context) -> None:
        """Pre-compute all indicators (vectorized)."""
        bars = ctx.bars
        p = self.p

        # MESA MAMA/FAMA
        self.mama, self.fama = ta_lib.mesa_mama_fama(
            bars.hl2, p.fast_limit, p.slow_limit
        )

        # Regime EMA
        self.regime_ema = ta_lib.ema(bars.close, p.regime_ema_len)

        # ATR
        self.atr_raw = ta_lib.atr(bars.high, bars.low, bars.close, p.atr_len)
        self.atr_pct = np.where(
            bars.close > 0,
            (self.atr_raw / bars.close) * 100.0,
            np.nan
        )

        # Crossovers (pre-computed boolean arrays)
        self.mama_cross_up = ta_lib.crossover(self.mama, self.fama)
        self.mama_cross_down = ta_lib.crossunder(self.mama, self.fama)

        # State variables (reset per backtest)
        self._pending_long = False
        self._pending_short = False
        self._trade_entry_price = np.nan
        self._trade_entry_bar = 0
        self._be_stop_active = False
        self._trade_max_pct = 0.0
        self._last_exit_bar = 0
        self._last_exit_dir = 0  # 1=long exit, -1=short exit
        self._last_action_bar = -1

    def on_bar(self, bar: BarData, ctx: Context) -> None:
        """Per-bar trading logic — mirrors Pine Script exactly."""
        i = ctx.bar_index
        p = self.p

        # Skip bars where indicators aren't ready
        if i < 7 or np.isnan(self.mama[i]) or np.isnan(self.fama[i]):
            return

        mama = self.mama[i]
        fama = self.fama[i]
        close = bar.close

        # ── ATR Filter ──
        min_atr_active = p.min_atr_pct_btc  # Hardcoded for BTCUSDT
        atr_pct = self.atr_pct[i]
        atr_ok = (not p.use_atr_filter or
                  (not np.isnan(atr_pct) and atr_pct + 1e-6 >= min_atr_active))

        # ── Regime Filter ──
        regime_ema = self.regime_ema[i]
        bull_regime = close > regime_ema if not np.isnan(regime_ema) else True
        bear_regime = close < regime_ema if not np.isnan(regime_ema) else True

        # ── Separation check ──
        diff_ok = (p.sep_threshold <= 0 or
                   abs(mama - fama) / close > p.sep_threshold)

        # ── Direction filter ──
        want_long = p.dir_mode != "Short only"
        want_short = p.dir_mode != "Long only"

        if p.use_regime_filter:
            if p.regime_mode == "Suppress counter-trend":
                if bear_regime:
                    want_long = False
                if bull_regime:
                    want_short = False
            else:
                want_long = want_long and bull_regime
                want_short = want_short and bear_regime

        # ── Pending signal tracking ──
        if self.mama_cross_up[i]:
            self._pending_long = True
            self._pending_short = False
        if self.mama_cross_down[i]:
            self._pending_short = True
            self._pending_long = False

        if self._pending_long and mama < fama:
            self._pending_long = False
        if self._pending_short and mama > fama:
            self._pending_short = False

        # ── Signal generation ──
        long_signal_changed = self._pending_long and diff_ok and want_long
        short_signal_changed = self._pending_short and diff_ok and want_short

        # ── Position state ──
        pos = ctx.position_size
        is_long = pos > 0
        is_short = pos < 0
        is_flat = pos == 0

        # ── Sizing ──
        margin_pct = p.pos_pct / 100.0
        target_notional = ctx.equity * margin_pct * p.max_leverage
        raw_qty = target_notional / close if close > 0 else 0
        min_lev_qty = (ctx.equity * margin_pct * p.min_leverage) / close if close > 0 else 0
        max_lev_qty = (ctx.equity * margin_pct * p.max_leverage) / close if close > 0 else 0
        clamped_qty = max(min_lev_qty, min(raw_qty, max_lev_qty))
        stepped = np.floor(clamped_qty / p.min_qty) * p.min_qty
        qty_contracts = max(p.min_qty, stepped)

        can_trade = atr_ok and qty_contracts > 0

        # ── Trade state tracking ──
        prev_pos = self._get_prev_pos(ctx)
        new_entry = (is_long and not (prev_pos > 0)) or (is_short and not (prev_pos < 0))
        position_closed = is_flat and not (prev_pos == 0)

        if new_entry:
            self._trade_entry_price = close
            self._trade_entry_bar = i
            self._be_stop_active = False
            self._trade_max_pct = 0.0

        if position_closed:
            self._trade_entry_price = np.nan
            self._trade_entry_bar = 0
            self._be_stop_active = False
            self._trade_max_pct = 0.0

        # ── Unrealized P&L tracking ──
        unreal_pct = 0.0
        if not np.isnan(self._trade_entry_price) and self._trade_entry_price > 0:
            if is_long:
                unreal_pct = (close - self._trade_entry_price) / self._trade_entry_price * 100.0
            elif is_short:
                unreal_pct = (self._trade_entry_price - close) / self._trade_entry_price * 100.0

        if unreal_pct > self._trade_max_pct:
            self._trade_max_pct = unreal_pct

        if p.use_be_stop and not self._be_stop_active and self._trade_max_pct >= p.be_trigger_pct:
            self._be_stop_active = True

        bars_held = i - self._trade_entry_bar
        hold_time_met = not p.use_min_hold or bars_held >= p.min_hold_bars

        # ── Exit signal evaluation ──

        # Breakeven stop
        be_stop_hit = False
        if p.use_be_stop and self._be_stop_active and not np.isnan(self._trade_entry_price):
            if is_long:
                be_price = self._trade_entry_price * (1.0 + p.be_offset_pct / 100.0)
                if close <= be_price:
                    be_stop_hit = True
            elif is_short:
                be_price = self._trade_entry_price * (1.0 - p.be_offset_pct / 100.0)
                if close >= be_price:
                    be_stop_hit = True

        # MAMA trailing exit
        mama_trail_exit = False
        if p.use_mama_trail and unreal_pct >= p.mama_trail_min_pct:
            if is_long and close < mama:
                mama_trail_exit = True
            if is_short and close > mama:
                mama_trail_exit = True

        # Profit lock exit
        profit_lock_exit = False
        if (p.use_profit_lock and bars_held >= p.lock_bars
                and self._trade_max_pct >= p.lock_min_pct):
            lock_floor = self._trade_max_pct * (p.lock_retrace_pct / 100.0)
            if unreal_pct < lock_floor:
                profit_lock_exit = True

        # Standard MAMA/FAMA exit
        standard_exit_long = self.mama_cross_down[i] or (mama < fama and diff_ok)
        standard_exit_short = self.mama_cross_up[i] or (mama > fama and diff_ok)

        hold_expired_long = hold_time_met and is_long and mama < fama
        hold_expired_short = hold_time_met and is_short and mama > fama

        # Combined exit logic
        exit_long_signal = False
        exit_short_signal = False
        exit_reason = ""

        if is_long:
            if be_stop_hit:
                exit_long_signal = True
                exit_reason = "BE_STOP"
            elif hold_time_met and mama_trail_exit:
                exit_long_signal = True
                exit_reason = "MAMA_TRAIL"
            elif profit_lock_exit:
                exit_long_signal = True
                exit_reason = "PROFIT_LOCK"
            elif hold_time_met and standard_exit_long:
                exit_long_signal = True
                exit_reason = "CROSS_EXIT"
            elif hold_expired_long:
                exit_long_signal = True
                exit_reason = "CROSS_EXIT"

        if is_short:
            if be_stop_hit:
                exit_short_signal = True
                exit_reason = "BE_STOP"
            elif hold_time_met and mama_trail_exit:
                exit_short_signal = True
                exit_reason = "MAMA_TRAIL"
            elif profit_lock_exit:
                exit_short_signal = True
                exit_reason = "PROFIT_LOCK"
            elif hold_time_met and standard_exit_short:
                exit_short_signal = True
                exit_reason = "CROSS_EXIT"
            elif hold_expired_short:
                exit_short_signal = True
                exit_reason = "CROSS_EXIT"

        # Reversal signals
        can_reverse_long = hold_time_met and short_signal_changed and want_short and can_trade
        can_reverse_short = hold_time_met and long_signal_changed and want_long and can_trade

        # ── Trading logic (matches Pine barstate.isconfirmed block) ──
        can_act = i != self._last_action_bar
        reversed_this_bar = False

        if can_act:
            # CASE 1: Reversal Long → Short
            if (p.use_swing_logic and is_long and can_reverse_long
                    and not be_stop_hit):
                ctx.set_exit_reason("REVERSE")
                ctx.close_all()
                ctx.entry("Short", "short", qty=qty_contracts)
                reversed_this_bar = True
                self._last_action_bar = i
                self._pending_short = False

            # CASE 2: Reversal Short → Long
            elif (p.use_swing_logic and is_short and can_reverse_short
                      and not be_stop_hit):
                ctx.set_exit_reason("REVERSE")
                ctx.close_all()
                ctx.entry("Long", "long", qty=qty_contracts)
                reversed_this_bar = True
                self._last_action_bar = i
                self._pending_long = False

            # CASE 3: Exit Long
            elif is_long and exit_long_signal and not reversed_this_bar:
                can_reverse_on_exit = (p.use_swing_logic and exit_reason == "CROSS_EXIT"
                                       and mama < fama and want_short and can_trade)
                if can_reverse_on_exit:
                    ctx.set_exit_reason("EXIT_REVERSE")
                    ctx.close_all()
                    ctx.entry("Short", "short", qty=qty_contracts)
                    reversed_this_bar = True
                    self._last_action_bar = i
                else:
                    ctx.set_exit_reason(exit_reason)
                    ctx.close("Long")
                    self._last_action_bar = i
                    self._last_exit_bar = i
                    self._last_exit_dir = 1

            # CASE 4: Exit Short
            elif is_short and exit_short_signal and not reversed_this_bar:
                can_reverse_on_exit = (p.use_swing_logic and exit_reason == "CROSS_EXIT"
                                       and mama > fama and want_long and can_trade)
                if can_reverse_on_exit:
                    ctx.set_exit_reason("EXIT_REVERSE")
                    ctx.close_all()
                    ctx.entry("Long", "long", qty=qty_contracts)
                    reversed_this_bar = True
                    self._last_action_bar = i
                else:
                    ctx.set_exit_reason(exit_reason)
                    ctx.close("Short")
                    self._last_action_bar = i
                    self._last_exit_bar = i
                    self._last_exit_dir = -1

            # CASE 5: Non-swing exits
            elif not p.use_swing_logic:
                if is_long and mama < fama and hold_time_met:
                    ctx.set_exit_reason("NON_SWING")
                    ctx.close("Long")
                    self._last_action_bar = i
                if is_short and mama > fama and hold_time_met:
                    ctx.set_exit_reason("NON_SWING")
                    ctx.close("Short")
                    self._last_action_bar = i

            # CASE 6: Fresh entry from flat
            # Anti-churn cooldown
            long_cooldown = (self._last_exit_dir == 1 and
                            (i - self._last_exit_bar) < p.reentry_min_bars)
            short_cooldown = (self._last_exit_dir == -1 and
                             (i - self._last_exit_bar) < p.reentry_min_bars)

            if can_trade and not reversed_this_bar and is_flat:
                if long_signal_changed and not long_cooldown:
                    ctx.entry("Long", "long", qty=qty_contracts)
                    self._last_action_bar = i
                    self._pending_long = False
                elif short_signal_changed and not short_cooldown:
                    ctx.entry("Short", "short", qty=qty_contracts)
                    self._last_action_bar = i
                    self._pending_short = False

    def _get_prev_pos(self, ctx: Context) -> float:
        """Track previous bar's position size for new entry detection."""
        if not hasattr(self, '_prev_pos'):
            self._prev_pos = 0.0
        prev = self._prev_pos
        self._prev_pos = ctx.position_size
        return prev


# Allow direct execution for testing
if __name__ == "__main__":
    import sys
    import os

    # Add parent dir to path for imports
    sys.path.insert(0, os.path.dirname(__file__))

    from backtest_engine import BacktestEngine

    if len(sys.argv) < 2:
        print("Usage: python mesa_mama_fama.py <data_csv> [initial_capital] [fee_pct] [warmup_bars]")
        print("  fee_pct is per-side (e.g. 0.035 = 0.035%)")
        print("  warmup_bars: skip N bars for indicator convergence (default 0)")
        sys.exit(1)

    data_file = sys.argv[1]
    initial_capital = float(sys.argv[2]) if len(sys.argv) > 2 else 1_000_000.0
    fee_pct = float(sys.argv[3]) if len(sys.argv) > 3 else 0.035
    warmup_bars = int(sys.argv[4]) if len(sys.argv) > 4 else 0

    print(f"Loading data from {data_file}...")
    bars = Bars.from_csv(data_file)
    print(f"  {len(bars)} bars loaded")

    strategy = MesaMamaFama()
    params = strategy.get_param_defaults()

    print(f"Running backtest (capital=${initial_capital:,.0f}, fee={fee_pct}% per side, warmup={warmup_bars})...")
    result = BacktestEngine.run_detail(
        strategy, bars, params,
        initial_capital=initial_capital,
        fee_pct=fee_pct,
        warmup_bars=warmup_bars,
    )

    s = result.stats
    print(f"\n{'='*50}")
    print(f"  MESA MAMA FAMA v3.3 — Backtest Results")
    print(f"{'='*50}")
    print(f"  Net Profit:    ${s.net_profit:>12,.2f}")
    print(f"  Gross Profit:  ${s.gross_profit:>12,.2f}")
    print(f"  Gross Loss:    ${s.gross_loss:>12,.2f}")
    print(f"  Profit Factor: {s.profit_factor:>12.2f}")
    print(f"  Max Drawdown:  ${s.max_drawdown:>12,.2f} ({s.max_drawdown_pct:.1f}%)")
    print(f"  Win Rate:      {s.win_rate:>11.1f}%")
    print(f"  Total Trades:  {s.total_trades:>12d}")
    print(f"  Winners:       {s.winning_trades:>12d}")
    print(f"  Losers:        {s.losing_trades:>12d}")
    print(f"{'='*50}")

    # Print first 10 trades for comparison with TradingView
    if result.trades:
        print(f"\n  First 10 trades:")
        print(f"  {'#':>3} {'Dir':>5} {'Entry':>10} {'Exit':>10} {'Qty':>8} {'Net P&L':>12} {'Bars':>5}")
        for t in result.trades[:10]:
            print(f"  {t.trade_num:>3} {t.direction:>5} {t.entry_price:>10.1f} "
                  f"{t.exit_price:>10.1f} {t.qty:>8.3f} ${t.net_pnl:>11,.2f} {t.bars_held:>5}")
