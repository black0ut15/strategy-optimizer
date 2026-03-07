"""Larry Williams Combined System — hand-translated from Pine Script.

Pattern-based entry system with volatility breakout confirmation.
Uses multiple Larry Williams patterns (swing structure, consecutive closes,
pullbacks, outside bars, smash days) combined with a volatility range
breakout for entry timing.

Exit modes: Risk:Reward bracket, First Profitable Open, After N Bars.

Pine Script reference:
  strategy("Larry Williams Combined System", ...)
  process_orders_on_close=false
  default_qty_type=strategy.percent_of_equity
  default_qty_value=100
  commission_value=0.045
  pyramiding=0
"""

import numpy as np
from strategy_base import Strategy, Param
from backtest_engine import BarData, Context, Bars


class LarryWilliamsCombined(Strategy):
    params = {
        # Direction
        "dir_mode": Param("Both", options=["Both", "Long Only", "Short Only"],
                          group="Direction"),

        # Entry Patterns
        "use_swing_structure": Param(True, group="Entry Patterns"),
        "use_consec_down": Param(True, group="Entry Patterns"),
        "use_pullback": Param(True, group="Entry Patterns"),
        "use_outside_bar": Param(True, group="Entry Patterns"),
        "use_smash_day": Param(True, group="Entry Patterns"),
        "use_fade_three_up": Param(True, group="Entry Patterns"),
        "require_at_least_one": Param(True, group="Entry Patterns"),

        # Pattern Parameters
        "consec_bear_bars": Param(3, min=1, max=10, step=1,
                                  group="Pattern Parameters"),
        "consec_bull_bars": Param(3, min=1, max=10, step=1,
                                  group="Pattern Parameters"),
        "trend_lookback": Param(30, min=5, max=100, step=5,
                                group="Pattern Parameters"),
        "pullback_lookback": Param(9, min=1, max=50, step=1,
                                   group="Pattern Parameters"),
        "smash_lookback": Param(1, min=1, max=10, step=1,
                                group="Pattern Parameters"),

        # Volatility Breakout
        "vol_model": Param("Simple Previous Range",
                           options=["Simple Previous Range", "Swing-Based Range"],
                           group="Volatility Breakout"),
        "buy_mult": Param(0.50, min=0.0, max=2.0, step=0.05,
                          group="Volatility Breakout"),
        "sell_mult": Param(0.50, min=0.0, max=2.0, step=0.05,
                           group="Volatility Breakout"),

        # Stop Loss
        "stop_mode": Param("Range Percent",
                           options=["Range Percent", "Swing Extreme"],
                           group="Stop Loss"),
        "stop_mult": Param(0.50, min=0.0, max=2.0, step=0.05,
                           group="Stop Loss"),

        # Exit Mode
        "exit_mode": Param("Risk Reward",
                           options=["Risk Reward", "First Profitable Open", "After N Bars"],
                           group="Exit Mode"),
        "reward_ratio": Param(3.0, min=0.5, max=10.0, step=0.5,
                              group="Exit Mode"),
        "exit_after_n": Param(3, min=1, max=50, step=1,
                              group="Exit Mode"),

        # Sizing
        "qty_pct": Param(100.0, min=1.0, max=100.0, step=1.0,
                         group="Sizing"),
    }

    def init(self, ctx: Context) -> None:
        """No pre-computed indicators — all patterns are bar-relative."""
        self._bars_since_entry = 0

    def on_bar(self, bar: BarData, ctx: Context) -> None:
        """Per-bar trading logic — mirrors Pine Script exactly."""
        i = ctx.bar_index
        p = self.p
        bars = ctx.bars

        # Need at least enough history for all lookbacks
        min_bars = max(6, p.trend_lookback + 2, p.pullback_lookback + 2,
                       p.smash_lookback + 2, p.consec_bear_bars + 1,
                       p.consec_bull_bars + 1)
        if i < min_bars:
            return

        # ── Helpers ──
        O = bars.open
        H = bars.high
        L = bars.low
        C = bars.close

        # ── Track bars since entry ──
        if ctx.position_size != 0:
            self._bars_since_entry += 1
        else:
            self._bars_since_entry = 0

        # ══════════════════════════════════════════════
        # NON-R:R EXIT MODES (checked before entry logic)
        # Pine checks these at top of bar processing
        # ══════════════════════════════════════════════

        if p.exit_mode == "First Profitable Open" and ctx.position_size != 0 and self._bars_since_entry >= 1:
            if ctx.position_size > 0 and bar.open > ctx.position_avg_price:
                ctx.set_exit_reason("FPO")
                ctx.close_all(comment="FPO")
            elif ctx.position_size < 0 and bar.open < ctx.position_avg_price:
                ctx.set_exit_reason("FPO")
                ctx.close_all(comment="FPO")

        # After N Bars (MQ5: barsSinceEntry > exitAfterCandles, strictly greater)
        if p.exit_mode == "After N Bars" and ctx.position_size != 0:
            if self._bars_since_entry > p.exit_after_n:
                ctx.set_exit_reason("N-Bar Exit")
                ctx.close_all(comment="N-Bar Exit")

        # ══════════════════════════════════════════════
        # PATTERN DETECTION (on completed bars)
        # ══════════════════════════════════════════════

        # Short-Term Swing Low (3-bar pattern at bars [1,2,3])
        pat_st_low = False
        if p.use_swing_structure:
            is_swing = L[i-2] < L[i-1] and L[i-2] < L[i-3]
            is_outside = H[i-2] > H[i-3] and L[i-2] < L[i-3]
            is_inside = H[i-1] < H[i-2] and L[i-1] > L[i-2]
            pat_st_low = is_swing and not is_outside and not is_inside

        # Short-Term Swing High
        pat_st_high = False
        if p.use_swing_structure:
            is_swing = H[i-2] > H[i-1] and H[i-2] > H[i-3]
            is_outside = H[i-2] > H[i-3] and L[i-2] < L[i-3]
            is_inside = H[i-1] < H[i-2] and L[i-1] > L[i-2]
            pat_st_high = is_swing and not is_outside and not is_inside

        # Consecutive Bearish Closes (bars 1..N)
        pat_consec_down = False
        if p.use_consec_down:
            all_bear = True
            for j in range(1, p.consec_bear_bars + 1):
                if C[i-j] >= O[i-j]:
                    all_bear = False
                    break
            pat_consec_down = all_bear

        # Pullback in Uptrend
        pat_pullback = False
        if p.use_pullback:
            today_open = O[i-1]
            close_trend = C[i - 1 - p.trend_lookback]
            close_pullback = C[i - 1 - p.pullback_lookback]
            pat_pullback = today_open > close_trend and today_open < close_pullback

        # Outside Bar with Down Close
        pat_outside_bar = False
        if p.use_outside_bar:
            is_outside = H[i-1] > H[i-2] and L[i-1] < L[i-2]
            is_down_close = C[i-1] < L[i-2]
            is_bearish = O[i-1] > C[i-1]
            pat_outside_bar = is_outside and is_down_close and is_bearish

        # Smash Day Buy Reversal
        pat_smash_buy = False
        if p.use_smash_day:
            # Bar[1] must NOT be outside bar
            is_ob = H[i-1] > H[i-2] and L[i-1] < L[i-2]
            if not is_ob:
                all_below = True
                for j in range(2, p.smash_lookback + 2):
                    if C[i-1] >= L[i-j]:
                        all_below = False
                        break
                pat_smash_buy = all_below

        # Smash Day Sell Reversal
        pat_smash_sell = False
        if p.use_smash_day:
            is_ob = H[i-1] > H[i-2] and L[i-1] < L[i-2]
            if not is_ob:
                all_above = True
                for j in range(2, p.smash_lookback + 2):
                    if C[i-1] <= H[i-j]:
                        all_above = False
                        break
                pat_smash_sell = all_above

        # Fade Consecutive Up Closes
        pat_consec_up = False
        if p.use_fade_three_up:
            all_bull = True
            for j in range(1, p.consec_bull_bars + 1):
                if C[i-j] <= O[i-j]:
                    all_bull = False
                    break
            pat_consec_up = all_bull

        # ── Aggregate signals ──
        any_bullish = (pat_st_low or pat_consec_down or pat_pullback
                       or pat_outside_bar or pat_smash_buy)
        any_bearish = pat_st_high or pat_consec_up or pat_smash_sell

        long_pattern_ok = any_bullish if p.require_at_least_one else True
        short_pattern_ok = any_bearish if p.require_at_least_one else True

        # ══════════════════════════════════════════════
        # VOLATILITY RANGE
        # ══════════════════════════════════════════════

        if p.vol_model == "Simple Previous Range":
            working_range = H[i-1] - L[i-1]
        else:  # Swing-Based Range
            swing_a = abs(H[i-4] - L[i-1])
            swing_b = abs(H[i-2] - L[i-4])
            working_range = max(swing_a, swing_b)

        if working_range <= 0:
            return

        # ══════════════════════════════════════════════
        # ENTRY LEVELS
        # ══════════════════════════════════════════════

        today_open = bar.open  # Pine: todayOpen = open (current bar)
        buy_entry = today_open + working_range * p.buy_mult
        sell_entry = today_open - working_range * p.sell_mult

        # ══════════════════════════════════════════════
        # STOP LOSS
        # ══════════════════════════════════════════════

        if p.stop_mode == "Range Percent":
            bull_stop = buy_entry - working_range * p.stop_mult
            bear_stop = sell_entry + working_range * p.stop_mult
        else:  # Swing Extreme
            bull_stop = L[i-2]
            bear_stop = H[i-2]

        # Smash day overrides
        smash_bull_stop = L[i-1]
        smash_bear_stop = H[i-1]

        eff_bull_stop = smash_bull_stop if (pat_smash_buy and not pat_st_low) else bull_stop
        eff_bear_stop = smash_bear_stop if (pat_smash_sell and not pat_st_high) else bear_stop

        # ══════════════════════════════════════════════
        # TAKE PROFIT (R:R)
        # ══════════════════════════════════════════════

        bull_risk = max(buy_entry - eff_bull_stop, working_range * 0.001)
        bear_risk = max(eff_bear_stop - sell_entry, working_range * 0.001)
        bull_tp = buy_entry + bull_risk * p.reward_ratio
        bear_tp = sell_entry - bear_risk * p.reward_ratio

        # ══════════════════════════════════════════════
        # DIRECTION & CONDITIONS
        # ══════════════════════════════════════════════

        allow_long = p.dir_mode != "Short Only"
        allow_short = p.dir_mode != "Long Only"
        no_position = ctx.position_size == 0

        long_condition = (long_pattern_ok and allow_long and no_position
                         and working_range > 0)
        short_condition = (short_pattern_ok and allow_short and no_position
                          and working_range > 0)

        # ══════════════════════════════════════════════
        # BREAKOUT ENTRY
        # ══════════════════════════════════════════════

        # Pine: close > buyEntry (checked on each bar)
        # With process_orders_on_close=false, entry fills at next bar open.
        # But the bracket exit is set immediately.

        # Sizing: percent of equity
        qty_pct = p.qty_pct / 100.0
        equity = ctx.strategy_equity
        qty = (equity * qty_pct) / bar.close if bar.close > 0 else 0

        long_trigger = long_condition and bar.close > buy_entry
        short_trigger = short_condition and bar.close < sell_entry

        if long_trigger:
            ctx.entry("LW Long", "long", qty=qty)
            if p.exit_mode == "Risk Reward":
                ctx.exit("LW Long TP/SL", from_entry="LW Long",
                         stop=eff_bull_stop, limit=bull_tp)
            else:
                ctx.exit("LW Long SL", from_entry="LW Long",
                         stop=eff_bull_stop)

        if short_trigger:
            ctx.entry("LW Short", "short", qty=qty)
            if p.exit_mode == "Risk Reward":
                ctx.exit("LW Short TP/SL", from_entry="LW Short",
                         stop=eff_bear_stop, limit=bear_tp)
            else:
                ctx.exit("LW Short SL", from_entry="LW Short",
                         stop=eff_bear_stop)


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import os

    sys.path.insert(0, os.path.dirname(__file__))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from backtest_engine import BacktestEngine
    from data_manager import DataManager

    if len(sys.argv) < 2:
        print("Usage: python larry_williams.py <data_csv> [initial_capital] [fee_pct] [warmup_bars]")
        print("  fee_pct is per-side (e.g. 0.045 = 0.045%)")
        print("  warmup_bars: 0=none, -1=auto, N=explicit")
        sys.exit(1)

    data_file = sys.argv[1]
    initial_capital = float(sys.argv[2]) if len(sys.argv) > 2 else 10_000.0
    fee_pct = float(sys.argv[3]) if len(sys.argv) > 3 else 0.045
    warmup_arg = int(sys.argv[4]) if len(sys.argv) > 4 else 0

    dm = DataManager()
    bars, warmup_bars = dm.load_csv(data_file, interval="5m", warmup_bars=warmup_arg)

    strategy = LarryWilliamsCombined()
    params = strategy.get_param_defaults()

    print(f"Running backtest (capital=${initial_capital:,.0f}, fee={fee_pct}%, warmup={warmup_bars})...")
    result = BacktestEngine.run_detail(
        strategy, bars, params,
        initial_capital=initial_capital,
        fee_pct=fee_pct,
        warmup_bars=warmup_bars,
    )

    s = result.stats
    print(f"\n{'='*50}")
    print(f"  Larry Williams Combined — Backtest Results")
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

    if result.trades:
        print(f"\n  First 10 trades:")
        print(f"  {'#':>3} {'Dir':>5} {'Entry':>10} {'Exit':>10} {'Qty':>8} {'Net P&L':>12} {'Bars':>5} {'Reason':>12}")
        for t in result.trades[:10]:
            print(f"  {t.trade_num:>3} {t.direction:>5} {t.entry_price:>10.1f} "
                  f"{t.exit_price:>10.1f} {t.qty:>8.4f} ${t.net_pnl:>11,.2f} "
                  f"{t.bars_held:>5} {t.exit_reason:>12}")
