"""Auto-translated from Pine Script: Pure ORB [MES 3ct]"""

import numpy as np
from strategy_base import Strategy, Param
from backtest_engine import BarData, Context, Bars
import ta as ta_lib


class PureOrbTranslated(Strategy):
    """Translated from: Pure ORB [MES 3ct]"""

    params = {
        "i_mode": Param("Both", options=['Both', 'Long Only', 'Short Only'], group="Strategy"),
        "i_max_trades_per_day": Param(1, min=1.0, max=5.0, group="Strategy"),
        "i_qty": Param(3, min=1.0, max=20.0, group="Strategy"),
        "i_confirm_close": Param(True, group="Strategy"),
        "i_or_minutes": Param(15, min=1.0, max=120.0, group="Session"),
        "i_cash_session": Param("0830-1500", group="Session"),
        "i_flatten_session": Param("1455-1500", group="Session"),
        "i_entry_session": Param("0830-1100", group="Session"),
        "i_sl_mode": Param("Opposite Side", options=['Opposite Side', 'OR Midpoint', 'ATR'], group="Stop Loss"),
        "i_sl_pad": Param(0.0, min=0.0, max=50.0, step=0.25, group="Stop Loss"),
        "i_sl_atr_mult": Param(1.5, min=0.5, max=5.0, step=0.1, group="Stop Loss"),
        "i_tp_mode": Param("R Multiple", options=['R Multiple', 'OR Multiple', 'ATR Multiple', 'None'], group="Take Profit"),
        "i_tp_rmult": Param(2.0, min=0.5, max=10.0, step=0.25, group="Take Profit"),
        "i_tp_or_mult": Param(2.0, min=0.5, max=10.0, step=0.25, group="Take Profit"),
        "i_tp_atr_mult": Param(3.0, min=0.5, max=10.0, step=0.5, group="Take Profit"),
        "i_use_trail": Param(True, group="Trailing Stop"),
        "i_trail_atr_mult": Param(1.2, min=0.3, max=5.0, step=0.1, group="Trailing Stop"),
        "i_trail_after_r": Param(0.75, min=0.1, max=5.0, step=0.25, group="Trailing Stop"),
        "i_use_be": Param(True, group="Breakeven"),
        "i_be_after_r": Param(0.5, min=0.1, max=3.0, step=0.1, group="Breakeven"),
        "i_use_time_stop": Param(True, group="Time Stop"),
        "i_time_stop_bars": Param(12, min=1.0, max=100.0, group="Time Stop"),
        "i_atr_len": Param(14, min=5.0, max=50.0, group="ATR"),
        "i_show_or": Param(True, group="Visuals"),
        "i_show_levels": Param(True, group="Visuals"),
        "i_show_tpsl": Param(True, group="Visuals"),
        "i_show_table": Param(True, group="Visuals"),
    }

    def init(self, ctx: Context) -> None:
        """Pre-compute indicators (vectorized)."""
        bars = ctx.bars
        p = self.p

        self._prevInSession = False
        self._prevDOM = -1
        self._orHigh = 0.0
        self._orLow = 0.0
        self._orMid = 0.0
        self._orLocked = False
        self._orRange = 0.0
        self._orBarCount = 0
        self._entryBar = 0
        self._entryPrice = 0.0
        self._initialSL = 0.0
        self._initialRisk = 0.0
        self._initialTP = 0.0
        self._trailActive = False
        self._trailStopPrice = 0.0
        self._tradesToday = 0

        self._prev_pos = 0.0
        self.atrVal = ta_lib.atr(bars.high, bars.low, bars.close, p.i_atr_len)
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

    def _f_longSL(self, bar, ctx, bars, i, p):
        """Translated from Pine: f_longSL()"""
        # switch p.i_sl_mode:  (translated to if/elif chain below)
            # case Opposite Side: self._orLow - p.i_sl_pad
            # case OR Midpoint: self._orMid - p.i_sl_pad
        return # default: longEntry - atrVal * p.i_sl_atr_mult

    def _f_shortSL(self, bar, ctx, bars, i, p):
        """Translated from Pine: f_shortSL()"""
        # switch p.i_sl_mode:  (translated to if/elif chain below)
            # case Opposite Side: self._orHigh + p.i_sl_pad
            # case OR Midpoint: self._orMid + p.i_sl_pad
        return # default: shortEntry + atrVal * p.i_sl_atr_mult

    def _f_longTP(self, bar, ctx, bars, i, p, _entry, _risk):
        """Translated from Pine: f_longTP()"""
        result = np.nan
        # switch p.i_tp_mode:  (translated to if/elif chain below)
            # case R Multiple: result := _entry + _risk * p.i_tp_rmult
            # case OR Multiple: result := _entry + (_risk if np.isnan(self._orRange) else self._orRange) * p.i_tp_or_mult
            # case ATR Multiple: result := _entry + atrVal * p.i_tp_atr_mult
        return result

    def _f_shortTP(self, bar, ctx, bars, i, p, _entry, _risk):
        """Translated from Pine: f_shortTP()"""
        result = np.nan
        # switch p.i_tp_mode:  (translated to if/elif chain below)
            # case R Multiple: result := _entry - _risk * p.i_tp_rmult
            # case OR Multiple: result := _entry - (_risk if np.isnan(self._orRange) else self._orRange) * p.i_tp_or_mult
            # case ATR Multiple: result := _entry - atrVal * p.i_tp_atr_mult
        return result

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

        inSession = not np.isnan(self._pine_time("5", p.i_cash_session))
        isFlattenTime = not np.isnan(self._pine_time("5", p.i_flatten_session))
        inEntryWindow = not np.isnan(self._pine_time("5", p.i_entry_session))
        isSessionFirstBar = inSession and not self._prevInSession
        self._prevInSession = inSession
        curDOM = self._bar_dayofmonth(bar)
        isNewDay = curDOM != self._prevDOM
        if isNewDay:
            self._prevDOM = curDOM
        barMins = 300 / 60
        if isSessionFirstBar:
            self._orHigh = bar.high
            self._orLow = bar.low
            self._orLocked = False
            self._orRange = np.nan
            self._orMid = np.nan
            self._orBarCount = 1
        elif inSession and not self._orLocked:
            self._orBarCount += 1
            self._orHigh = max((bar.high if np.isnan(self._orHigh) else self._orHigh), bar.high)
            self._orLow = min((bar.low if np.isnan(self._orLow) else self._orLow), bar.low)
        if inSession and not self._orLocked and self._orBarCount * barMins >= p.i_or_minutes:
            self._orLocked = True
            self._orRange = self._orHigh - self._orLow
            self._orMid = (self._orHigh + self._orLow) / 2.0
        longEntry = self._orHigh
        shortEntry = self._orLow
        if isNewDay:
            self._tradesToday = 0
        canTrade = self._orLocked and inSession and inEntryWindow and not isFlattenTime and isFlat and self._tradesToday < p.i_max_trades_per_day
        canGoLong = p.i_mode == "Both" or p.i_mode == "Long Only"
        canGoShort = p.i_mode == "Both" or p.i_mode == "Short Only"
        longTrigger = (bar.close > longEntry if p.i_confirm_close else bar.high > longEntry)
        shortTrigger = (bar.close < shortEntry if p.i_confirm_close else bar.low < shortEntry)
        longCond = canTrade and canGoLong and not np.isnan(longEntry) and not np.isnan(self._orLow) and longTrigger
        if longCond:
            _sl = self._f_longSL(bar, ctx, bars, i, p)
            _risk = abs(bar.close - _sl)
            _tp = self._f_longTP(bar, ctx, bars, i, p, bar.close, _risk)
            self._entryBar = ctx.bar_index
            self._entryPrice = bar.close
            self._initialSL = _sl
            self._initialRisk = _risk
            self._initialTP = _tp
            self._trailActive = False
            self._trailStopPrice = np.nan
            self._tradesToday += 1
        shortCond = canTrade and canGoShort and not np.isnan(shortEntry) and not np.isnan(self._orHigh) and shortTrigger
        if shortCond:
            _sl = self._f_shortSL(bar, ctx, bars, i, p)
            _risk = abs(_sl - bar.close)
            _tp = self._f_shortTP(bar, ctx, bars, i, p, bar.close, _risk)
            self._entryBar = ctx.bar_index
            self._entryPrice = bar.close
            self._initialSL = _sl
            self._initialRisk = _risk
            self._initialTP = _tp
            self._trailActive = False
            self._trailStopPrice = np.nan
            self._tradesToday += 1
        barsSinceEntry = ctx.bar_index - self._entryBar
        currentR = 0.0
        if isLong and self._initialRisk > 0:
            currentR = (bar.close - self._entryPrice) / self._initialRisk
        if isShort and self._initialRisk > 0:
            currentR = (self._entryPrice - bar.close) / self._initialRisk
        if p.i_use_be and not isFlat and self._initialRisk > 0 and currentR >= p.i_be_after_r:
            beSL = self._entryPrice + ((p.i_sl_pad if isLong else -p.i_sl_pad))
        if p.i_use_trail and not isFlat and self._initialRisk > 0 and currentR >= p.i_trail_after_r:
            self._trailActive = True
        if self._trailActive and not isFlat:
            trailOffset = self.atrVal[i] * p.i_trail_atr_mult
            if isLong:
                newTrail = bar.close - trailOffset
                self._trailStopPrice = (newTrail if np.isnan(self._trailStopPrice) else max((0 if np.isnan(self._trailStopPrice) else self._trailStopPrice), newTrail))
            if isShort:
                newTrail = bar.close + trailOffset
                self._trailStopPrice = (newTrail if np.isnan(self._trailStopPrice) else min((0 if np.isnan(self._trailStopPrice) else self._trailStopPrice), newTrail))
        if isFlat:
            self._trailActive = False
            self._trailStopPrice = np.nan
        showLvl = self._orLocked and p.i_show_levels and inSession and not isFlattenTime
        inTrade = not isFlat and p.i_show_tpsl
        if inTrade and p.i_use_time_stop and p.i_show_tpsl:
            barsLeft = p.i_time_stop_bars - barsSinceEntry

        # Track position for next bar's new-entry detection
        self._prev_pos = ctx.position_size
