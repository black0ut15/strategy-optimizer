"""Auto-translated from Pine Script: Larry Williams Combined System"""

import numpy as np
from strategy_base import Strategy, Param
from backtest_engine import BarData, Context, Bars
import ta as ta_lib


class LwCombinedTranslated(Strategy):
    """Translated from: Larry Williams Combined System"""

    params = {
        "direction_input": Param("Both", options=['Long Only', 'Short Only', 'Both'], group="Direction"),
        "use_swing_structure": Param(True, group="Entry Patterns"),
        "use_consec_down": Param(True, group="Entry Patterns"),
        "use_pullback": Param(True, group="Entry Patterns"),
        "use_outside_bar": Param(True, group="Entry Patterns"),
        "use_smash_day": Param(True, group="Entry Patterns"),
        "use_fade_three_up": Param(True, group="Entry Patterns"),
        "require_at_least_one": Param(True, group="Entry Patterns"),
        "consec_bear_bars": Param(3, min=1.0, max=10.0, group="Pattern Parameters"),
        "consec_bull_bars": Param(3, min=1.0, max=10.0, group="Pattern Parameters"),
        "trend_lookback": Param(30, min=5.0, max=100.0, group="Pattern Parameters"),
        "pullback_lookback": Param(9, min=1.0, max=50.0, group="Pattern Parameters"),
        "smash_lookback": Param(1, min=1.0, max=10.0, group="Pattern Parameters"),
        "vol_model": Param("Simple Previous Range", options=['Simple Previous Range', 'Swing-Based Range'], group="Volatility Breakout"),
        "buy_mult": Param(0.5, min=0.0, step=0.05, group="Volatility Breakout"),
        "sell_mult": Param(0.5, min=0.0, step=0.05, group="Volatility Breakout"),
        "stop_mode": Param("Range Percent", options=['Range Percent', 'Swing Extreme'], group="Stop Loss"),
        "stop_mult": Param(0.5, min=0.0, step=0.05, group="Stop Loss"),
        "exit_mode": Param("Risk Reward", options=['Risk Reward', 'First Profitable Open', 'After N Bars'], group="Exit Mode"),
        "reward_ratio": Param(3.0, min=0.5, step=0.5, group="Exit Mode"),
        "exit_after_n": Param(3, min=1.0, max=50.0, group="Exit Mode"),
        "use_tdw": Param(False, group="Trade Day of Week"),
        "trade_sun": Param(False, group="Trade Day of Week"),
        "trade_mon": Param(True, group="Trade Day of Week"),
        "trade_tue": Param(False, group="Trade Day of Week"),
        "trade_wed": Param(False, group="Trade Day of Week"),
        "trade_thu": Param(False, group="Trade Day of Week"),
        "trade_fri": Param(False, group="Trade Day of Week"),
        "trade_sat": Param(False, group="Trade Day of Week"),
        "use_time_filter": Param(False, group="Time of Day Filter"),
        "start_hour": Param(9, min=0.0, max=23.0, group="Time of Day Filter"),
        "start_min": Param(30, min=0.0, max=59.0, group="Time of Day Filter"),
        "end_hour": Param(16, min=0.0, max=23.0, group="Time of Day Filter"),
        "end_min": Param(0, min=0.0, max=59.0, group="Time of Day Filter"),
        "show_levels": Param(True, group="Visual"),
        "show_patterns": Param(True, group="Visual"),
    }

    def init(self, ctx: Context) -> None:
        """Pre-compute indicators (vectorized)."""
        bars = ctx.bars
        p = self.p

        self._bullStop = 0.0
        self._bearStop = 0.0
        self._barsSinceEntry = 0

        self._prev_pos = 0.0
        # TODO: Pre-compute indicators here
        # Example: self.ema = ta_lib.ema(bars.close, p.ema_len)

    def _bar_hour(self, bar):
        from datetime import datetime, timezone
        ts = bar.timestamp / 1000 if bar.timestamp > 1e12 else bar.timestamp
        return datetime.fromtimestamp(ts, tz=timezone.utc).hour

    def _bar_minute(self, bar):
        from datetime import datetime, timezone
        ts = bar.timestamp / 1000 if bar.timestamp > 1e12 else bar.timestamp
        return datetime.fromtimestamp(ts, tz=timezone.utc).minute

    def _bar_dayofweek(self, bar):
        from datetime import datetime, timezone
        ts = bar.timestamp / 1000 if bar.timestamp > 1e12 else bar.timestamp
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoweekday()  # 1=Mon, 7=Sun

    def _bar_dayofmonth(self, bar):
        from datetime import datetime, timezone
        ts = bar.timestamp / 1000 if bar.timestamp > 1e12 else bar.timestamp
        return datetime.fromtimestamp(ts, tz=timezone.utc).day

    @staticmethod
    def _pine_time(timeframe, session_str):
        """Stub for Pine time(timeframe, session) — returns non-NaN if in session."""
        # TODO: Implement session time checking based on bar timestamp
        return 1.0  # Default: always in session

    def _size_at_fill(self, fill_price, equity_at_fill):
        """Recalculate position size at fill time (matches TradingView calc_on_order_fills)."""
        p = self.p
        if fill_price <= 0:
            return 0.0
        return 1.0

    def _f_simpleRange(self, bar, ctx, bars, i, p):
        """Translated from Pine: f_simpleRange()"""
        return bars.high[i-1] - bars.low[i-1]

    def _f_swingRange(self, bar, ctx, bars, i, p):
        """Translated from Pine: f_swingRange()"""
        swingA = abs(bars.high[i-4] - bars.low[i-1])
        swingB = abs(bars.high[i-2] - bars.low[i-4])
        return max(swingA, swingB)

    def _f_isSTLow(self, bar, ctx, bars, i, p):
        """Translated from Pine: f_isSTLow()"""
        isSwing = bars.low[i-2] < bars.low[i-1] and bars.low[i-2] < bars.low[i-3]
        isOutside = bars.high[i-2] > bars.high[i-3] and bars.low[i-2] < bars.low[i-3]
        isInside = bars.high[i-1] < bars.high[i-2] and bars.low[i-1] > bars.low[i-2]
        return isSwing and not isOutside and not isInside

    def _f_isSTHigh(self, bar, ctx, bars, i, p):
        """Translated from Pine: f_isSTHigh()"""
        isSwing = bars.high[i-2] > bars.high[i-1] and bars.high[i-2] > bars.high[i-3]
        isOutside = bars.high[i-2] > bars.high[i-3] and bars.low[i-2] < bars.low[i-3]
        isInside = bars.high[i-1] < bars.high[i-2] and bars.low[i-1] > bars.low[i-2]
        return isSwing and not isOutside and not isInside

    def _f_consecDown(self, bar, ctx, bars, i, p, n):
        """Translated from Pine: f_consecDown()"""
        result = True
        for i in range(int(1), int(n) + 1):
            if bars.close[i] >= bars.open[i]:
                result = False
                break
        return result

    def _f_consecUp(self, bar, ctx, bars, i, p, n):
        """Translated from Pine: f_consecUp()"""
        result = True
        for i in range(int(1), int(n) + 1):
            if bars.close[i] <= bars.open[i]:
                result = False
                break
        return result

    def _f_pullbackInUptrend(self, bar, ctx, bars, i, p):
        """Translated from Pine: f_pullbackInUptrend()"""
        todayOpen = bars.open[i-1]
        closeTrendBar = bars.close[1 + p.trend_lookback]
        closePullbackBar = bars.close[1 + p.pullback_lookback]
        return todayOpen > closeTrendBar and todayOpen < closePullbackBar

    def _f_outsideBarDownClose(self, bar, ctx, bars, i, p):
        """Translated from Pine: f_outsideBarDownClose()"""
        isOutside = bars.high[i-1] > bars.high[i-2] and bars.low[i-1] < bars.low[i-2]
        isDownClose = bars.close[i-1] < bars.low[i-2]
        isBearish = bars.open[i-1] > bars.close[i-1]
        return isOutside and isDownClose and isBearish

    def _f_isOutsideBar(self, bar, ctx, bars, i, p, idx):
        """Translated from Pine: f_isOutsideBar()"""
        return bars.high[idx] > bars.high[idx + 1] and bars.low[idx] < bars.low[idx + 1]

    def _f_smashDayBuy(self, bar, ctx, bars, i, p):
        """Translated from Pine: f_smashDayBuy()"""
        if self._f_isOutsideBar(bar, ctx, bars, i, p, 1):
            False
        else:
            allBelow = True
            for i in range(int(2), int(p.smash_lookback) + 1):
                if bars.close[i-1] >= bars.low[i]:
                    allBelow = False
                    break
        return allBelow

    def _f_smashDaySell(self, bar, ctx, bars, i, p):
        """Translated from Pine: f_smashDaySell()"""
        if self._f_isOutsideBar(bar, ctx, bars, i, p, 1):
            False
        else:
            allAbove = True
            for i in range(int(2), int(p.smash_lookback) + 1):
                if bars.close[i-1] <= bars.high[i]:
                    allAbove = False
                    break
        return allAbove

    def _f_tdwAllowed(self, bar, ctx, bars, i, p):
        """Translated from Pine: f_tdwAllowed()"""
        if not p.use_tdw:
            True
        else:
            dow = self._bar_dayofweek(bar)
            # switch dow:  (translated to if/elif chain below)
                # case 1: p.trade_sun
                # case 2: p.trade_mon
                # case 3: p.trade_tue
                # case 4: p.trade_wed
                # case 5: p.trade_thu
                # case 6: p.trade_fri
                # case 7: p.trade_sat
        return # default: False

    def _f_timeAllowed(self, bar, ctx, bars, i, p):
        """Translated from Pine: f_timeAllowed()"""
        if not p.use_time_filter:
            True
        else:
            h = self._bar_hour(bar)
            m = self._bar_minute(bar)
            currentMins = h * 60 + m
            startMins = p.start_hour * 60 + p.start_min
            endMins = p.end_hour * 60 + p.end_min
        return currentMins >= startMins and currentMins <= endMins

    def on_bar(self, bar: BarData, ctx: Context) -> None:
        """Per-bar trading logic."""
        i = ctx.bar_index
        p = self.p
        bars = ctx.bars

        # Position state tracking (Pine does this implicitly)
        prev_position_size = self._prev_pos
        isLong = ctx.position_size > 0
        isShort = ctx.position_size < 0
        isFlat = ctx.position_size == 0
        newEntry = ((isLong and prev_position_size <= 0) or (isShort and prev_position_size >= 0)) and not isFlat
        positionClosed = isFlat and prev_position_size != 0

        allowLong = p.direction_input == "Both" or p.direction_input == "Long Only"
        allowShort = p.direction_input == "Both" or p.direction_input == "Short Only"
        workingRange = (self._f_simpleRange(bar, ctx, bars, i, p) if p.vol_model == "Simple Previous Range" else self._f_swingRange(bar, ctx, bars, i, p))
        patSTLow = p.use_swing_structure and self._f_isSTLow(bar, ctx, bars, i, p)
        patSTHigh = p.use_swing_structure and self._f_isSTHigh(bar, ctx, bars, i, p)
        patConsecDown = p.use_consec_down     and self._f_consecDown(bar, ctx, bars, i, p, p.consec_bear_bars)
        patPullback = p.use_pullback       and self._f_pullbackInUptrend(bar, ctx, bars, i, p)
        patOutsideBar = p.use_outside_bar     and self._f_outsideBarDownClose(bar, ctx, bars, i, p)
        patSmashBuy = p.use_smash_day       and self._f_smashDayBuy(bar, ctx, bars, i, p)
        patSmashSell = p.use_smash_day       and self._f_smashDaySell(bar, ctx, bars, i, p)
        patConsecUp = p.use_fade_three_up    and self._f_consecUp(bar, ctx, bars, i, p, p.consec_bull_bars)
        anyBullish = patSTLow or patConsecDown or patPullback or patOutsideBar or patSmashBuy
        anyBearish = patSTHigh or patConsecUp or patSmashSell
        longPatternOK = (anyBullish if p.require_at_least_one else True)
        shortPatternOK = (anyBearish if p.require_at_least_one else True)
        todayOpen = bar.open
        buyEntry = todayOpen + (workingRange * p.buy_mult)
        sellEntry = todayOpen - (workingRange * p.sell_mult)
        if p.stop_mode == "Range Percent":
            self._bullStop = buyEntry - (workingRange * p.stop_mult)
            self._bearStop = sellEntry + (workingRange * p.stop_mult)
        else:
            self._bullStop = bars.low[i-2]
            self._bearStop = bars.high[i-2]
        smashBullStop = bars.low[i-1]
        smashBearStop = bars.high[i-1]
        effBullStop = (smashBullStop if patSmashBuy  and not patSTLow else self._bullStop)
        effBearStop = (smashBearStop if patSmashSell and not patSTHigh else self._bearStop)
        bullRisk = max(buyEntry  - effBullStop, workingRange * 0.001)
        bearRisk = max(effBearStop - sellEntry, workingRange * 0.001)
        bullTP = buyEntry  + (bullRisk * p.reward_ratio)
        bearTP = sellEntry - (bearRisk * p.reward_ratio)
        filtersOK = self._f_tdwAllowed(bar, ctx, bars, i, p) and self._f_timeAllowed(bar, ctx, bars, i, p)
        noPosition = ctx.position_size == 0
        longCondition = longPatternOK and allowLong  and filtersOK and noPosition and workingRange > 0
        shortCondition = shortPatternOK and allowShort and filtersOK and noPosition and workingRange > 0
        longTrigger = longCondition  and bar.close > buyEntry
        shortTrigger = shortCondition and bar.close < sellEntry
        if longTrigger:
            if p.exit_mode == "Risk Reward":
                ctx.entry("LW Long", "long")
                ctx.exit("LW Long TP/SL", from_entry="LW Long", stop=effBullStop, limit=bullTP)
            else:
                ctx.entry("LW Long", "long")
                ctx.exit("LW Long SL", from_entry="LW Long", stop=effBullStop)
        if shortTrigger:
            if p.exit_mode == "Risk Reward":
                ctx.entry("LW Short", "short")
                ctx.exit("LW Short TP/SL", from_entry="LW Short", stop=effBearStop, limit=bearTP)
            else:
                ctx.entry("LW Short", "short")
                ctx.exit("LW Short SL", from_entry="LW Short", stop=effBearStop)
        if ctx.position_size != 0:
            self._barsSinceEntry += 1
        else:
            self._barsSinceEntry = 0
        if p.exit_mode == "First Profitable Open" and ctx.position_size != 0 and self._barsSinceEntry >= 1:
            if ctx.position_size > 0 and bar.open > ctx.position_avg_price:
                ctx.close("LW Long")
            if ctx.position_size < 0 and bar.open < ctx.position_avg_price:
                ctx.close("LW Short")
        if p.exit_mode == "After N Bars" and ctx.position_size != 0:
            if self._barsSinceEntry > p.exit_after_n:
                ctx.close_all(comment="N-Bar Exit")

        # Track position for next bar's new-entry detection
        self._prev_pos = ctx.position_size
