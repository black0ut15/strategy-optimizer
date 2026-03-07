"""Auto-translated from Pine Script: MESA MAMA FAMA Crypto v3.3 BTC"""

import numpy as np
from strategy_base import Strategy, Param
from backtest_engine import BarData, Context, Bars
import ta as ta_lib


class MesaRegressionTest(Strategy):
    """Translated from: MESA MAMA FAMA Crypto v3.3 BTC"""

    params = {
        "dir_mode": Param("Both", options=['Both', 'Long only', 'Short only'], group="Trade Logic"),
        "flip_mode": Param("Allow at close", options=['Allow at close', 'Disallow same-bar flip', 'Auto (<23m disallow)'], group="Trade Logic"),
        "use_swing_logic": Param(True, group="Trade Logic"),
        "use_bestop": Param(True, group="Exit Management"),
        "be_trigger_pct": Param(0.3, step=0.01, group="Exit Management"),
        "be_offset_pct": Param(0.02, step=0.01, group="Exit Management"),
        "use_mama_trail": Param(True, group="Exit Management"),
        "mama_trail_min_pct": Param(0.15, step=0.01, group="Exit Management"),
        "use_profit_lock": Param(True, group="Exit Management"),
        "lock_bars": Param(48, min=0.0, group="Exit Management"),
        "lock_min_pct": Param(0.15, step=0.01, group="Exit Management"),
        "lock_retrace_pct": Param(50.0, min=10.0, max=90.0, step=5.0, group="Exit Management"),
        "use_min_hold": Param(True, group="Exit Management"),
        "min_hold_bars": Param(144, min=0.0, group="Exit Management"),
        "use_regime_filter": Param(True, group="Regime Filter"),
        "regime_ema_len": Param(200, min=10.0, group="Regime Filter"),
        "regime_mode": Param("Suppress counter-trend", options=['Suppress counter-trend', 'Trend-aligned only'], group="Regime Filter"),
        "tv_token": Param("mySuperSecretWebhook123", group="Maker Payload (for Node Bridge)"),
        "send_alerts": Param(True, group="Maker Payload (for Node Bridge)"),
        "entry_offset_pct_in": Param(0.02, step=0.01, group="Maker Payload (for Node Bridge)"),
        "exit_offset_pct_in": Param(0.01, step=0.01, group="Maker Payload (for Node Bridge)"),
        "tp_enabled_in": Param(True, group="Maker Payload (for Node Bridge)"),
        "tp_pct_in": Param(0.5, step=0.01, group="Maker Payload (for Node Bridge)"),
        "pos_pct": Param(0.1, step=0.001, group="Sizing (Margin % of Equity)"),
        "lvxmax": Param(5.0, group="Sizing (Margin % of Equity)"),
        "lvxmin": Param(1.0, group="Sizing (Margin % of Equity)"),
        "use_fractional_qty": Param(True, group="Contract Settings"),
        "min_qty": Param(0.001, step=0.001, group="Contract Settings"),
        "contract_value": Param(1.0, step=1e-06, group="Contract Settings"),
        "min_contracts": Param(1, min=1.0, group="Contract Settings"),
        "max_contracts": Param(1000000, min=1.0, group="Contract Settings"),
        "use_atr_filter": Param(True, group="ATR Filter"),
        "atr_len": Param(14, min=1.0, group="ATR Filter"),
        "use_symbol_presets": Param(True, group="ATR Filter"),
        "min_atr_pct_global": Param(0.1, step=0.01, group="ATR Filter"),
        "min_atr_pct_btc": Param(0.07, step=0.01, group="ATR Filter"),
        "min_atr_pct_eth": Param(0.09, step=0.01, group="ATR Filter"),
        "min_atr_pct_sol": Param(0.12, step=0.01, group="ATR Filter"),
        "min_atr_pct_avax": Param(0.14, step=0.01, group="ATR Filter"),
        "min_atr_pct_ada": Param(0.1, step=0.01, group="ATR Filter"),
        "min_atr_pct_xrp": Param(0.1, step=0.01, group="ATR Filter"),
        "min_atr_pct_ltc": Param(0.09, step=0.01, group="ATR Filter"),
        "mesa_src": Param("hl2", options=['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4', 'hlcc4'], group="MESA Settings"),
        "fast_limit": Param(0.38, min=0.01, max=1.0, group="MESA Settings"),
        "slow_limit": Param(0.035, min=0.001, max=0.5, group="MESA Settings"),
        "sep_threshold": Param(0.003, step=0.0001, group="MESA Settings"),
        "show_alert_labels": Param(True, group="Visual / Labels"),
        "show_diag_table": Param(True, group="Visual / Labels"),
        "show_exit_labels": Param(True, group="Visual / Labels"),
    }

    def init(self, ctx: Context) -> None:
        """Pre-compute indicators (vectorized)."""
        bars = ctx.bars
        p = self.p

        self._pendingLong = False
        self._pendingShort = False
        self._cooldownBars = 0
        self._tradeEntryPrice = 0.0
        self._tradeEntryBar = 0
        self._beStopActive = False
        self._tradeMaxPct = 0.0
        self._lastExitBar = 0
        self._lastExitDir = 0
        self._lastActionBar = -1
        self._lastLabelBar = -1

        self._prev_pos = 0.0
        self.atr = ta_lib.atr(bars.high, bars.low, bars.close, p.atr_len)
        self.regimeEma = ta_lib.ema(bars.close, p.regime_ema_len)
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
        """Recalculate position size at fill time (matches TradingView calc_on_order_fills)."""
        p = self.p
        if fill_price <= 0:
            return 0.0
        target_notional = equity_at_fill * p.pos_pct * p.lvxmax
        raw_qty = target_notional / fill_price
        lo = (equity_at_fill * p.pos_pct * p.lvxmin) / fill_price
        hi = (equity_at_fill * p.pos_pct * p.lvxmax) / fill_price
        clamped = max(lo, min(raw_qty, hi))
        import numpy as np
        stepped = np.floor(clamped / p.min_qty) * p.min_qty
        return max(p.min_qty, stepped)

    def _f_clamp(self, bar, ctx, bars, _bi, p, _x, _lo, _hi):
        """Translated from Pine: f_clamp()"""
        return max(_lo, min(_x, _hi))

    def _f_mama_fama_crypto(self, bar, ctx, bars, _bi, p, _src, _fast, _slow):
        """Translated from Pine: f_mama_fama_crypto()"""
        smooth = (4.0*_src + 3.0*(0 if np.isnan(_src[1]) else _src[1]) + 2.0*(0 if np.isnan(_src[2]) else _src[2]) + (0 if np.isnan(_src[3]) else _src[3])) / 10.0
        detrender = np.nan
        I1 = np.nan
        Q1 = np.nan
        jI = np.nan
        jQ = np.nan
        I2 = np.nan
        Q2 = np.nan
        Re = np.nan
        Im = np.nan
        period = 0.0
        phase = 0.0
        deltaPhase = 0.0
        alpha = 0.0
        mama = np.nan
        fama = np.nan
        adj = 0.075*(0 if np.isnan(period[1]) else period[1]) + 0.54
        detrender = (0.0962*smooth + 0.5769*(0 if np.isnan(smooth[2]) else smooth[2]) - 0.5769*(0 if np.isnan(smooth[4]) else smooth[4]) - 0.0962*(0 if np.isnan(smooth[6]) else smooth[6])) * adj
        Q1 = (0.0962*detrender + 0.5769*(0 if np.isnan(detrender[2]) else detrender[2]) - 0.5769*(0 if np.isnan(detrender[4]) else detrender[4]) - 0.0962*(0 if np.isnan(detrender[6]) else detrender[6])) * adj
        I1 = (0 if np.isnan(detrender[3]) else detrender[3])
        jI = (0.0962*I1 + 0.5769*(0 if np.isnan(I1[2]) else I1[2]) - 0.5769*(0 if np.isnan(I1[4]) else I1[4]) - 0.0962*(0 if np.isnan(I1[6]) else I1[6])) * adj
        jQ = (0.0962*Q1 + 0.5769*(0 if np.isnan(Q1[2]) else Q1[2]) - 0.5769*(0 if np.isnan(Q1[4]) else Q1[4]) - 0.0962*(0 if np.isnan(Q1[6]) else Q1[6])) * adj
        I2 = I1 - jQ
        Q2 = Q1 + jI
        I2 = 0.2*I2 + 0.8*(0 if np.isnan(I2[1]) else I2[1])
        Q2 = 0.2*Q2 + 0.8*(0 if np.isnan(Q2[1]) else Q2[1])
        Re = I2*(0 if np.isnan(I2[1]) else I2[1]) + Q2*(0 if np.isnan(Q2[1]) else Q2[1])
        Im = I2*(0 if np.isnan(Q2[1]) else Q2[1]) - Q2*(0 if np.isnan(I2[1]) else I2[1])
        Re = 0.2*Re + 0.8*(0 if np.isnan(Re[1]) else Re[1])
        Im = 0.2*Im + 0.8*(0 if np.isnan(Im[1]) else Im[1])
        p1 = (0 if np.isnan(period[1]) else period[1])
        if (Im != 0 and Re != 0):
            p1 = 2.0*np.pi / np.arctan(Im / Re)
        p1 = max(p1, 0.67 * (0 if np.isnan(period[1]) else period[1]))
        p1 = min(p1, 1.5 * (0 if np.isnan(period[1]) else period[1]))
        p1 = self._f_clamp(bar, ctx, bars, _bi, p, p1, 6.0, 50.0)
        period = 0.2*p1 + 0.8*(0 if np.isnan(period[1]) else period[1])
        phase = ((np.arctan(Q1 / I1) * 180.0 / np.pi) if (I1 != 0) else (0 if np.isnan(phase[1]) else phase[1]))
        deltaPhase = (0 if np.isnan(phase[1]) else phase[1]) - phase
        if deltaPhase < 1:
            deltaPhase = 1
        alpha = _fast / deltaPhase
        alpha = self._f_clamp(bar, ctx, bars, _bi, p, alpha, _slow, _fast)
        mama = alpha*_src + (1 - alpha)*(0 if np.isnan(mama[1]) else mama[1])
        fama = 0.5*alpha*mama + (1 - 0.5*alpha)*(0 if np.isnan(fama[1]) else fama[1])
        return [mama, fama, alpha, deltaPhase, period]

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
        if i < 202:
            self._prev_pos = ctx.position_size
            return

        # Position state tracking (Pine does this implicitly)
        prev_position_size = self._prev_pos
        isLong = ctx.position_size > 0
        isShort = ctx.position_size < 0
        isFlat = ctx.position_size == 0
        newEntry = ((isLong and prev_position_size <= 0) or (isShort and prev_position_size >= 0)) and not isFlat
        positionClosed = isFlat and prev_position_size != 0

        levTarget = p.lvxmax
        equity = ctx.strategy_equity
        targetMargin = equity * p.pos_pct
        targetNotional = targetMargin * levTarget
        qtyContracts = 0.0
        if p.use_fractional_qty:
            rawQty = targetNotional / bar.close
            minLevQty = (equity * p.pos_pct * p.lvxmin) / bar.close
            maxLevQty = (equity * p.pos_pct * p.lvxmax) / bar.close
            clampedQty = max(minLevQty, min(rawQty, maxLevQty))
            stepped = np.floor(clampedQty / p.min_qty) * p.min_qty
            qtyContracts = max(p.min_qty, stepped)
        else:
            contractsRaw = ((targetNotional / (p.contract_value * bar.close)) if (p.contract_value > 0 and bar.close > 0) else 0.0)
            contractsInt = np.floor(contractsRaw)
            contractsMinLevRaw = (((equity * p.pos_pct) * p.lvxmin) / (p.contract_value * bar.close) if (p.contract_value > 0 and bar.close > 0) else 0.0)
            contractsMaxLevRaw = (((equity * p.pos_pct) * p.lvxmax) / (p.contract_value * bar.close) if (p.contract_value > 0 and bar.close > 0) else 0.0)
            contractsFloatClamped = max(contractsMinLevRaw, min(contractsInt, contractsMaxLevRaw))
            qtyContracts = max(p.min_contracts, min(np.floor(contractsFloatClamped), p.max_contracts))
        symRaw = "BTCUSDT"
        symNorm = symRaw.replace(".P", "")
        atrPct = ((self.atr[i] / bar.close) * 100.0 if bar.close > 0 else np.nan)
        minAtrPctActive = p.min_atr_pct_global
        if p.use_symbol_presets:
            if symNorm == "BTCUSDT":
                minAtrPctActive = p.min_atr_pct_btc
            elif symNorm == "ETHUSDT":
                minAtrPctActive = p.min_atr_pct_eth
            elif symNorm == "SOLUSDT":
                minAtrPctActive = p.min_atr_pct_sol
            elif symNorm == "AVAXUSDT":
                minAtrPctActive = p.min_atr_pct_avax
            elif symNorm == "ADAUSDT":
                minAtrPctActive = p.min_atr_pct_ada
            elif symNorm == "XRPUSDT":
                minAtrPctActive = p.min_atr_pct_xrp
            elif symNorm == "LTCUSDT":
                minAtrPctActive = p.min_atr_pct_ltc
            else:
                minAtrPctActive = p.min_atr_pct_global
        else:
            minAtrPctActive = p.min_atr_pct_global
        atrEps = 1e-6
        atrOk = not p.use_atr_filter or (not np.isnan(atrPct) and atrPct + atrEps >= minAtrPctActive)
        mama, fama, alpha, deltaPhase, period = self._f_mama_fama_crypto(bar, ctx, bars, i, p, bars.get_source(p.mesa_src), p.fast_limit, p.slow_limit)
        bullRegime = (bar.close > self.regimeEma[i]) if not np.isnan(self.regimeEma[i]) else True
        bearRegime = (bar.close < self.regimeEma[i]) if not np.isnan(self.regimeEma[i]) else True
        diffOk = (True if p.sep_threshold <= 0 else (abs(mama - fama) / bar.close > p.sep_threshold))
        wantLong = p.dir_mode != "Short only"
        wantShort = p.dir_mode != "Long only"
        if p.use_regime_filter:
            if p.regime_mode == "Suppress counter-trend":
                if bearRegime:
                    wantLong = False
                if bullRegime:
                    wantShort = False
            else:
                wantLong = wantLong and bullRegime
                wantShort = wantShort and bearRegime
        mamaCrossUp = ta_lib.crossover(mama, fama)
        mamaCrossDown = ta_lib.crossunder(mama, fama)
        if mamaCrossUp:
            self._pendingLong = True
            self._pendingShort = False
        if mamaCrossDown:
            self._pendingShort = True
            self._pendingLong = False
        if self._pendingLong and mama < fama:
            self._pendingLong = False
        if self._pendingShort and mama > fama:
            self._pendingShort = False
        longSignal = mama > fama and wantLong
        shortSignal = mama < fama and wantShort
        longSignalChanged = self._pendingLong and diffOk and wantLong
        shortSignalChanged = self._pendingShort and diffOk and wantShort
        tfSeconds = timeframe.in_seconds("5")
        tfm = (np.nan if np.isnan(tfSeconds) else tfSeconds / 60.0)
        autoBlock = not np.isnan(tfm) and tfm < 23
        blockSameBarFlip = p.flip_mode == "Disallow same-bar flip" or (p.flip_mode == "Auto (<23m disallow)" and autoBlock)
        if barstate.isnew and self._cooldownBars > 0:
            self._cooldownBars -= 1
        canTrade = self._cooldownBars == 0 and atrOk and qtyContracts > 0
        reentryMinBars = 6
        if newEntry:
            self._tradeEntryPrice = bar.close
            self._tradeEntryBar = ctx.bar_index
            self._beStopActive = False
            self._tradeMaxPct = 0.0
        if positionClosed:
            self._tradeEntryPrice = np.nan
            self._tradeEntryBar = 0
            self._beStopActive = False
            self._tradeMaxPct = 0.0
        unrealPct = 0.0
        if not np.isnan(self._tradeEntryPrice) and self._tradeEntryPrice > 0:
            if isLong:
                unrealPct = (bar.close - self._tradeEntryPrice) / self._tradeEntryPrice * 100.0
            elif isShort:
                unrealPct = (self._tradeEntryPrice - bar.close) / self._tradeEntryPrice * 100.0
        if unrealPct > self._tradeMaxPct:
            self._tradeMaxPct = unrealPct
        if p.use_bestop and not self._beStopActive and self._tradeMaxPct >= p.be_trigger_pct:
            self._beStopActive = True
        barsHeld = ctx.bar_index - self._tradeEntryBar
        holdTimeMet = not p.use_min_hold or barsHeld >= p.min_hold_bars
        beExitPrice = np.nan
        if p.use_bestop and self._beStopActive and not np.isnan(self._tradeEntryPrice):
            if isLong:
                beExitPrice = self._tradeEntryPrice * (1.0 + p.be_offset_pct / 100.0)
            elif isShort:
                beExitPrice = self._tradeEntryPrice * (1.0 - p.be_offset_pct / 100.0)
        beStopHit = False
        if not np.isnan(beExitPrice):
            if isLong and bar.close <= beExitPrice:
                beStopHit = True
            if isShort and bar.close >= beExitPrice:
                beStopHit = True
        mamaTrailExit = False
        if p.use_mama_trail and unrealPct >= p.mama_trail_min_pct:
            if isLong and bar.close < mama:
                mamaTrailExit = True
            if isShort and bar.close > mama:
                mamaTrailExit = True
        profitLockExit = False
        if p.use_profit_lock and barsHeld >= p.lock_bars and self._tradeMaxPct >= p.lock_min_pct:
            lockFloor = self._tradeMaxPct * (p.lock_retrace_pct / 100.0)
            if unrealPct < lockFloor:
                profitLockExit = True
        standardExitLong = mamaCrossDown or (mama < fama and diffOk)
        standardExitShort = mamaCrossUp or (mama > fama and diffOk)
        holdExpiredAgainstLong = holdTimeMet and isLong and mama < fama
        holdExpiredAgainstShort = holdTimeMet and isShort and mama > fama
        exitLongSignal = False
        exitShortSignal = False
        exitReason = ""
        if isLong:
            if beStopHit:
                exitLongSignal = True
                exitReason = "BE_STOP"
            elif holdTimeMet and mamaTrailExit:
                exitLongSignal = True
                exitReason = "MAMA_TRAIL"
            elif profitLockExit:
                exitLongSignal = True
                exitReason = "PROFIT_LOCK"
            elif holdTimeMet and standardExitLong:
                exitLongSignal = True
                exitReason = "CROSS_EXIT"
            elif holdExpiredAgainstLong:
                exitLongSignal = True
                exitReason = "CROSS_EXIT"
        if isShort:
            if beStopHit:
                exitShortSignal = True
                exitReason = "BE_STOP"
            elif holdTimeMet and mamaTrailExit:
                exitShortSignal = True
                exitReason = "MAMA_TRAIL"
            elif profitLockExit:
                exitShortSignal = True
                exitReason = "PROFIT_LOCK"
            elif holdTimeMet and standardExitShort:
                exitShortSignal = True
                exitReason = "CROSS_EXIT"
            elif holdExpiredAgainstShort:
                exitShortSignal = True
                exitReason = "CROSS_EXIT"
        canReverseLong = holdTimeMet and shortSignalChanged and wantShort and canTrade
        canReverseShort = holdTimeMet and longSignalChanged and wantLong and canTrade
        canActThisBar = ctx.bar_index != self._lastActionBar
        reversedThisBar = False
        exitedThisBar = False
        if barstate.isconfirmed and canActThisBar:
            if p.use_swing_logic and isLong and canReverseLong and not beStopHit:
                ctx.close_all(comment="")
                ctx.entry("Short", "short", qty=qtyContracts, qty_func=self._size_at_fill)
                reversedThisBar = True
                self._lastActionBar = ctx.bar_index
                self._pendingShort = False
                if p.show_alert_labels and ctx.bar_index != self._lastLabelBar:
                    self._lastLabelBar = ctx.bar_index
            elif p.use_swing_logic and isShort and canReverseShort and not beStopHit:
                ctx.close_all(comment="")
                ctx.entry("Long", "long", qty=qtyContracts, qty_func=self._size_at_fill)
                reversedThisBar = True
                self._lastActionBar = ctx.bar_index
                self._pendingLong = False
                if p.show_alert_labels and ctx.bar_index != self._lastLabelBar:
                    self._lastLabelBar = ctx.bar_index
            elif isLong and exitLongSignal and not reversedThisBar:
                canReverseOnExit = p.use_swing_logic and exitReason == "CROSS_EXIT" and mama < fama and wantShort and canTrade
                if canReverseOnExit:
                    ctx.close_all(comment="")
                    ctx.entry("Short", "short", qty=qtyContracts, qty_func=self._size_at_fill)
                    reversedThisBar = True
                    self._lastActionBar = ctx.bar_index
                    if p.show_alert_labels and ctx.bar_index != self._lastLabelBar:
                        self._lastLabelBar = ctx.bar_index
                else:
                    ctx.close("Long")
                    exitedThisBar = True
                    self._lastActionBar = ctx.bar_index
                    self._lastExitBar = ctx.bar_index
                    self._lastExitDir = 1
                    exitLabel = ((("PROFIT LOCK EXIT" if "MAMA TRAIL EXIT" if "BE EXIT" if exitReason == "BE_STOP" else exitReason == "MAMA_TRAIL" else exitReason == "PROFIT_LOCK" else "EXIT")))
                    if p.show_alert_labels and ctx.bar_index != self._lastLabelBar:
                        self._lastLabelBar = ctx.bar_index
            elif isShort and exitShortSignal and not reversedThisBar:
                canReverseOnExitS = p.use_swing_logic and exitReason == "CROSS_EXIT" and mama > fama and wantLong and canTrade
                if canReverseOnExitS:
                    ctx.close_all(comment="")
                    ctx.entry("Long", "long", qty=qtyContracts, qty_func=self._size_at_fill)
                    reversedThisBar = True
                    self._lastActionBar = ctx.bar_index
                    if p.show_alert_labels and ctx.bar_index != self._lastLabelBar:
                        self._lastLabelBar = ctx.bar_index
                else:
                    ctx.close("Short")
                    exitedThisBar = True
                    self._lastActionBar = ctx.bar_index
                    self._lastExitBar = ctx.bar_index
                    self._lastExitDir = -1
                    exitLabel = ((("PROFIT LOCK EXIT" if "MAMA TRAIL EXIT" if "BE EXIT" if exitReason == "BE_STOP" else exitReason == "MAMA_TRAIL" else exitReason == "PROFIT_LOCK" else "EXIT")))
                    if p.show_alert_labels and ctx.bar_index != self._lastLabelBar:
                        self._lastLabelBar = ctx.bar_index
            elif not p.use_swing_logic:
                if isLong and mama < fama and holdTimeMet:
                    ctx.close("Long")
                    exitedThisBar = True
                    self._lastActionBar = ctx.bar_index
                if isShort and mama > fama and holdTimeMet:
                    ctx.close("Short")
                    exitedThisBar = True
                    self._lastActionBar = ctx.bar_index
            longCooldown = self._lastExitDir == 1 and (ctx.bar_index - self._lastExitBar) < reentryMinBars
            shortCooldown = self._lastExitDir == -1 and (ctx.bar_index - self._lastExitBar) < reentryMinBars
            if canTrade and not reversedThisBar and isFlat:
                if longSignalChanged and not longCooldown:
                    ctx.entry("Long", "long", qty=qtyContracts, qty_func=self._size_at_fill)
                    self._lastActionBar = ctx.bar_index
                    self._pendingLong = False
                    if p.show_alert_labels and ctx.bar_index != self._lastLabelBar:
                        self._lastLabelBar = ctx.bar_index
                elif shortSignalChanged and not shortCooldown:
                    ctx.entry("Short", "short", qty=qtyContracts, qty_func=self._size_at_fill)
                    self._lastActionBar = ctx.bar_index
                    self._pendingShort = False
                    if p.show_alert_labels and ctx.bar_index != self._lastLabelBar:
                        self._lastLabelBar = ctx.bar_index
        if (reversedThisBar or exitedThisBar) and blockSameBarFlip:
            self._cooldownBars = 1
        impliedNotional = (qtyContracts * bar.close if p.use_fractional_qty else qtyContracts * p.contract_value * bar.close)
        impliedLevVsMargin = (impliedNotional / targetMargin if targetMargin > 0 else np.nan)
        marginPctShown = p.pos_pct * 100
        clampText = ("FRAC" if p.use_fractional_qty else "INT")

        # Track position for next bar's new-entry detection
        self._prev_pos = ctx.position_size
