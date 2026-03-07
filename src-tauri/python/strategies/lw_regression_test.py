"""Auto-translated from Pine Script: Larry Williams Combined System"""

import numpy as np
from strategy_base import Strategy, Param
from backtest_engine import BarData, Context, Bars
import ta as ta_lib


class LwRegressionTest(Strategy):
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

    def _pine_time(self, timeframe, session_str):
        """Check if current bar falls within a session time window.
        
        Pine time(timeframe, session) returns non-NaN if in session.
        session_str format: "HHMM-HHMM" (e.g., "0830-1500")
        Uses exchange timezone (CT for futures, ET for stocks).
        """
        import numpy as np
        if not hasattr(self, '_current_bar_ts'):
            return np.nan
        from datetime import datetime, timezone, timedelta
        ts = self._current_bar_ts
        if ts > 1e12: ts = ts / 1000
        dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
        # Convert to CT (UTC-6, or UTC-5 during DST)
        # Simple DST: Mar second Sun to Nov first Sun
        year = dt_utc.year
        mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
        mar_second_sun = mar1 + timedelta(days=(6-mar1.weekday())%7 + 7)
        nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
        nov_first_sun = nov1 + timedelta(days=(6-nov1.weekday())%7)
        is_dst = mar_second_sun <= dt_utc.replace(tzinfo=timezone.utc) < nov_first_sun
        ct_offset = timedelta(hours=-5 if is_dst else -6)
        dt_ct = dt_utc + ct_offset
        bar_hhmm = dt_ct.hour * 100 + dt_ct.minute
        try:
            parts = session_str.split('-')
            start_hhmm = int(parts[0])
            end_hhmm = int(parts[1])
        except:
            return np.nan
        if start_hhmm <= end_hhmm:
            in_session = start_hhmm <= bar_hhmm < end_hhmm
        else:
            in_session = bar_hhmm >= start_hhmm or bar_hhmm < end_hhmm
        return 1.0 if in_session else np.nan

    def _size_at_fill(self, fill_price, equity_at_fill):
        """Sizing: 100.0% of equity."""
        if fill_price <= 0: return 0.0
        pct = 100.0 / 100.0
        return max(0.001, equity_at_fill * pct / fill_price)

    def _f_simpleRange(self, bar, ctx, bars, _bi, p):
        """Translated from Pine: f_simpleRange()"""
        return bars.high[_bi-1] - bars.low[_bi-1]

    def _f_swingRange(self, bar, ctx, bars, _bi, p):
        """Translated from Pine: f_swingRange()"""
        swingA = abs(bars.high[_bi-4] - bars.low[_bi-1])
        swingB = abs(bars.high[_bi-2] - bars.low[_bi-4])
        return max(swingA, swingB)

    def _f_isSTLow(self, bar, ctx, bars, _bi, p):
        """Translated from Pine: f_isSTLow()"""
        isSwing = bars.low[_bi-2] < bars.low[_bi-1] and bars.low[_bi-2] < bars.low[_bi-3]
        isOutside = bars.high[_bi-2] > bars.high[_bi-3] and bars.low[_bi-2] < bars.low[_bi-3]
        isInside = bars.high[_bi-1] < bars.high[_bi-2] and bars.low[_bi-1] > bars.low[_bi-2]
        return isSwing and not isOutside and not isInside

    def _f_isSTHigh(self, bar, ctx, bars, _bi, p):
        """Translated from Pine: f_isSTHigh()"""
        isSwing = bars.high[_bi-2] > bars.high[_bi-1] and bars.high[_bi-2] > bars.high[_bi-3]
        isOutside = bars.high[_bi-2] > bars.high[_bi-3] and bars.low[_bi-2] < bars.low[_bi-3]
        isInside = bars.high[_bi-1] < bars.high[_bi-2] and bars.low[_bi-1] > bars.low[_bi-2]
        return isSwing and not isOutside and not isInside

    def _f_consecDown(self, bar, ctx, bars, _bi, p, n):
        """Translated from Pine: f_consecDown()"""
        result = True
        for i in range(int(1), int(n) + 1):
            if bars.close[_bi-(i)] >= bars.open[_bi-(i)]:
                result = False
                break
        return result

    def _f_consecUp(self, bar, ctx, bars, _bi, p, n):
        """Translated from Pine: f_consecUp()"""
        result = True
        for i in range(int(1), int(n) + 1):
            if bars.close[_bi-(i)] <= bars.open[_bi-(i)]:
                result = False
                break
        return result

    def _f_pullbackInUptrend(self, bar, ctx, bars, _bi, p):
        """Translated from Pine: f_pullbackInUptrend()"""
        todayOpen = bars.open[_bi-1]
        closeTrendBar = bars.close[_bi-(1 + p.trend_lookback)]
        closePullbackBar = bars.close[_bi-(1 + p.pullback_lookback)]
        return todayOpen > closeTrendBar and todayOpen < closePullbackBar

    def _f_outsideBarDownClose(self, bar, ctx, bars, _bi, p):
        """Translated from Pine: f_outsideBarDownClose()"""
        isOutside = bars.high[_bi-1] > bars.high[_bi-2] and bars.low[_bi-1] < bars.low[_bi-2]
        isDownClose = bars.close[_bi-1] < bars.low[_bi-2]
        isBearish = bars.open[_bi-1] > bars.close[_bi-1]
        return isOutside and isDownClose and isBearish

    def _f_isOutsideBar(self, bar, ctx, bars, _bi, p, idx):
        """Translated from Pine: f_isOutsideBar()"""
        return bars.high[_bi-(idx)] > bars.high[_bi-(idx + 1)] and bars.low[_bi-(idx)] < bars.low[_bi-(idx + 1)]

    def _f_smashDayBuy(self, bar, ctx, bars, _bi, p):
        """Translated from Pine: f_smashDayBuy()"""
        if self._f_isOutsideBar(bar, ctx, bars, _bi, p, 1):
            return False
        else:
            allBelow = True
            for i in range(int(2), int(p.smash_lookback + 1) + 1):
                if bars.close[_bi-1] >= bars.low[_bi-(i)]:
                    allBelow = False
                    break
            return allBelow

    def _f_smashDaySell(self, bar, ctx, bars, _bi, p):
        """Translated from Pine: f_smashDaySell()"""
        if self._f_isOutsideBar(bar, ctx, bars, _bi, p, 1):
            return False
        else:
            allAbove = True
            for i in range(int(2), int(p.smash_lookback + 1) + 1):
                if bars.close[_bi-1] <= bars.high[_bi-(i)]:
                    allAbove = False
                    break
            return allAbove

    def _f_tdwAllowed(self, bar, ctx, bars, _bi, p):
        """Translated from Pine: f_tdwAllowed()"""
        if not p.use_tdw:
            return True
        else:
            dow = self._bar_dayofweek(bar)
            if dow == "1":
                return p.trade_sun
            elif dow == "2":
                return p.trade_mon
            elif dow == "3":
                return p.trade_tue
            elif dow == "4":
                return p.trade_wed
            elif dow == "5":
                return p.trade_thu
            elif dow == "6":
                return p.trade_fri
            elif dow == "7":
                return p.trade_sat
            else:
                return False

    def _f_timeAllowed(self, bar, ctx, bars, _bi, p):
        """Translated from Pine: f_timeAllowed()"""
        if not p.use_time_filter:
            return True
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
        self._current_bar_ts = bar.timestamp

        # Date-change detection (for session-based strategies)
        from datetime import datetime, timezone, timedelta
        _ts = bar.timestamp / 1000 if bar.timestamp > 1e12 else bar.timestamp
        _dt_utc = datetime.fromtimestamp(_ts, tz=timezone.utc)
        _year = _dt_utc.year
        _mar1 = datetime(_year, 3, 1, tzinfo=timezone.utc)
        _mar_sun2 = _mar1 + timedelta(days=(6-_mar1.weekday())%7 + 7)
        _nov1 = datetime(_year, 11, 1, tzinfo=timezone.utc)
        _nov_sun1 = _nov1 + timedelta(days=(6-_nov1.weekday())%7)
        _is_dst = _mar_sun2 <= _dt_utc < _nov_sun1
        _ct_dt = _dt_utc + timedelta(hours=-5 if _is_dst else -6)
        _ct_date = _ct_dt.strftime('%Y-%m-%d')
        if not hasattr(self, '_prev_ct_date'): self._prev_ct_date = _ct_date
        isNewDay = _ct_date != self._prev_ct_date
        self._prev_ct_date = _ct_date

        # Warmup guard: skip bars without enough history
        if i < 32:
            self._prev_pos = ctx.position_size
            return

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
                ctx.entry("LW Long", "long", qty_func=self._size_at_fill)
                ctx.exit("LW Long TP/SL", from_entry="LW Long", stop=effBullStop, limit=bullTP)
            else:
                ctx.entry("LW Long", "long", qty_func=self._size_at_fill)
                ctx.exit("LW Long SL", from_entry="LW Long", stop=effBullStop)
        if shortTrigger:
            if p.exit_mode == "Risk Reward":
                ctx.entry("LW Short", "short", qty_func=self._size_at_fill)
                ctx.exit("LW Short TP/SL", from_entry="LW Short", stop=effBearStop, limit=bearTP)
            else:
                ctx.entry("LW Short", "short", qty_func=self._size_at_fill)
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
