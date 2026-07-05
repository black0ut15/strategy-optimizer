"""Auto-translated from Pine Script: MESA MAMA FAMA Crypto v3.6 BTC"""

import numpy as np
from strategy_base import Strategy, Param
from backtest_engine import BarData, Context, Bars
import ta as ta_lib


class MesaMamaFamaCryptoV36Btc(Strategy):
    """Translated from: MESA MAMA FAMA Crypto v3.6 BTC"""

    params = {
        "dir_mode": Param("Both", options=['Both', 'Long only', 'Short only'], group="Trade Logic"),
        "flip_mode": Param("Allow at close", options=['Allow at close', 'Disallow same-bar flip', 'Auto (<23m disallow)'], group="Trade Logic"),
        "use_swing_logic": Param(True, group="Trade Logic"),
        "use_bestop": Param(True, group="Exit Management"),
        "be_trigger_pct": Param(0.2, step=0.01, group="Exit Management"),
        "be_offset_pct": Param(0.03, step=0.01, group="Exit Management"),
        "use_mama_trail": Param(True, group="Tiered MAMA Trail"),
        "mama_trail_min_pct": Param(0.1, step=0.01, group="Tiered MAMA Trail"),
        "use_tier2": Param(True, group="Tiered MAMA Trail"),
        "tier2thresh_pct": Param(0.3, step=0.01, group="Tiered MAMA Trail"),
        "tier2retrace_pct": Param(50.0, step=5.0, group="Tiered MAMA Trail"),
        "use_tier3": Param(True, group="Tiered MAMA Trail"),
        "big_win_thresh_pct": Param(0.6, step=0.01, group="Tiered MAMA Trail"),
        "big_win_retrace_pct": Param(40.0, step=5.0, group="Tiered MAMA Trail"),
        "use_profit_lock": Param(True, group="Exit Management"),
        "lock_bars": Param(24, min=0.0, group="Exit Management"),
        "lock_min_pct": Param(0.2, step=0.01, group="Exit Management"),
        "lock_retrace_pct": Param(50.0, min=10.0, max=90.0, step=5.0, group="Exit Management"),
        "use_min_hold": Param(True, group="Exit Management"),
        "min_hold_bars": Param(180, min=0.0, group="Exit Management"),
        "use_time_filter": Param(False, group="Time-of-Day Filter (UTC)"),
        "block_hour1": Param(4, min=0.0, max=23.0, group="Time-of-Day Filter (UTC)"),
        "block_hour2": Param(21, min=0.0, max=23.0, group="Time-of-Day Filter (UTC)"),
        "use_block_hour2": Param(True, group="Time-of-Day Filter (UTC)"),
        "use_regime_filter": Param(True, group="Regime Filter"),
        "regime_ema_len": Param(150, min=10.0, group="Regime Filter"),
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
        "contract_value": Param(1.0, group="Contract Settings"),
        "min_contracts": Param(1, min=1.0, group="Contract Settings"),
        "max_contracts": Param(1000000, min=1.0, group="Contract Settings"),
        "use_atr_filter": Param(True, group="ATR Filter"),
        "atr_len": Param(8, min=1.0, group="ATR Filter"),
        "use_symbol_presets": Param(True, group="ATR Filter"),
        "min_atr_pct_global": Param(0.1, step=0.01, group="ATR Filter"),
        "min_atr_pct_btc": Param(0.06, step=0.01, group="ATR Filter"),
        "min_atr_pct_eth": Param(0.09, step=0.01, group="ATR Filter"),
        "min_atr_pct_sol": Param(0.12, step=0.01, group="ATR Filter"),
        "min_atr_pct_avax": Param(0.14, step=0.01, group="ATR Filter"),
        "min_atr_pct_ada": Param(0.1, step=0.01, group="ATR Filter"),
        "min_atr_pct_xrp": Param(0.1, step=0.01, group="ATR Filter"),
        "min_atr_pct_ltc": Param(0.09, step=0.01, group="ATR Filter"),
        "mesa_src": Param("hlcc4", options=['close', 'open', 'high', 'low', 'hl2', 'hlc3', 'ohlc4', 'hlcc4'], group="MESA Settings"),
        "fast_limit": Param(0.3, min=0.01, max=1.0, group="MESA Settings"),
        "slow_limit": Param(0.05, min=0.001, max=0.5, group="MESA Settings"),
        "sep_threshold": Param(0.002, step=0.0001, group="MESA Settings"),
        "show_alert_labels": Param(True, group="Visual / Labels"),
        "show_diag_table": Param(True, group="Visual / Labels"),
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
        
        # Pre-compute indicators
        self.atr = ta_lib.atr(bars.high, bars.low, bars.close, p.atr_len)
        self.regimeEma = ta_lib.ema(bars.close, p.regime_ema_len)
        
        # MESA MAMA/FAMA
        source = bars.get_source(p.mesa_src)
        self.mama, self.fama = ta_lib.mesa_mama_fama(source, p.fast_limit, p.slow_limit)
        
        # Cross signals
        self.mamaCrossUp = ta_lib.crossover(self.mama, self.fama)
        self.mamaCrossDown = ta_lib.crossunder(self.mama, self.fama)

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

    def on_bar(self, bar: BarData, ctx: Context) -> None:
        """Per-bar trading logic."""
        i = ctx.bar_index
        p = self.p
        bars = ctx.bars

        # Warmup guard: skip bars without enough history
        if i < 182:
            self._prev_pos = ctx.position_size
            return

        # Position state tracking (Pine does this implicitly)
        prev_position_size = self._prev_pos
        isLong = ctx.position_size > 0
        isShort = ctx.position_size < 0
        isFlat = ctx.position_size == 0
        newEntry = ((isLong and prev_position_size <= 0) or (isShort and prev_position_size >= 0)) and not isFlat
        positionClosed = isFlat and prev_position_size != 0

        # Sizing calculation
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

        # Symbol handling
        symRaw = "BTCUSDT"
        symNorm = symRaw.replace(".P", "")
        
        # ATR Filter
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
        
        atrEps = 1e-6
        atrOk = not p.use_atr_filter or (not np.isnan(atrPct) and atrPct + atrEps >= minAtrPctActive)

        # MESA values
        mama = self.mama[i]
        fama = self.fama[i]
        
        # Regime Filter
        bullRegime = (bar.close > self.regimeEma[i]) if not np.isnan(self.regimeEma[i]) else True
        bearRegime = (bar.close < self.regimeEma[i]) if not np.isnan(self.regimeEma[i]) else True

        # Signal Generation
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

        if i > 0 and self.mamaCrossUp[i]:
            self._pendingLong = True
            self._pendingShort = False
        if i > 0 and self.mamaCrossDown[i]:
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

        # Time-of-day filtering
        from datetime import datetime, timezone
        ts = bar.timestamp / 1000 if bar.timestamp > 1e12 else bar.timestamp
        utcHour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        hourBlock = p.use_time_filter and (utcHour == p.block_hour1 or (p.use_block_hour2 and utcHour == p.block_hour2))
        hourOk = not hourBlock

        canTrade = self._cooldownBars == 0 and atrOk and qtyContracts > 0

        # Trade State Management
        if newEntry:
            # Use the engine's actual fill price, not bar.close
            self._tradeEntryPrice = ctx.position_avg_price if ctx.position_avg_price > 0 else bar.close
            self._tradeEntryBar = ctx.bar_index
            self._beStopActive = False
            self._tradeMaxPct = 0.0

        if positionClosed:
            self._tradeEntryPrice = np.nan
            self._tradeEntryBar = 0
            self._beStopActive = False
            self._tradeMaxPct = 0.0

        unrealPct = 0.0
        peakUnrealPct = 0.0
        if not np.isnan(self._tradeEntryPrice) and self._tradeEntryPrice > 0:
            if isLong:
                unrealPct = (bar.close - self._tradeEntryPrice) / self._tradeEntryPrice * 100.0
                # Track peak using intrabar high (favorable excursion)
                peakUnrealPct = (bar.high - self._tradeEntryPrice) / self._tradeEntryPrice * 100.0
            elif isShort:
                unrealPct = (self._tradeEntryPrice - bar.close) / self._tradeEntryPrice * 100.0
                # Track peak using intrabar low (favorable excursion)
                peakUnrealPct = (self._tradeEntryPrice - bar.low) / self._tradeEntryPrice * 100.0

        if peakUnrealPct > self._tradeMaxPct:
            self._tradeMaxPct = peakUnrealPct

        if p.use_bestop and not self._beStopActive and self._tradeMaxPct >= p.be_trigger_pct:
            self._beStopActive = True

        barsHeld = ctx.bar_index - self._tradeEntryBar
        holdTimeMet = not p.use_min_hold or barsHeld >= p.min_hold_bars

        # Exit Signal Evaluation - Breakeven stop
        beExitPrice = np.nan
        if p.use_bestop and self._beStopActive and not np.isnan(self._tradeEntryPrice):
            if isLong:
                beExitPrice = self._tradeEntryPrice * (1.0 + p.be_offset_pct / 100.0)
            elif isShort:
                beExitPrice = self._tradeEntryPrice * (1.0 - p.be_offset_pct / 100.0)

        beStopHit = False
        if not np.isnan(beExitPrice):
            if isLong and bar.low <= beExitPrice:
                beStopHit = True
            if isShort and bar.high >= beExitPrice:
                beStopHit = True

        # Tiered MAMA Trail
        mamaTrailExit = False
        mamaTrailTier = ""
        if p.use_mama_trail:
            if p.use_tier3 and self._tradeMaxPct >= p.big_win_thresh_pct:
                lockFloor3 = self._tradeMaxPct * (p.big_win_retrace_pct / 100.0)
                if unrealPct < lockFloor3:
                    mamaTrailExit = True
                    mamaTrailTier = "T3"
            elif p.use_tier2 and unrealPct >= p.tier2thresh_pct:
                lockFloor2 = self._tradeMaxPct * (p.tier2retrace_pct / 100.0)
                if unrealPct < lockFloor2:
                    mamaTrailExit = True
                    mamaTrailTier = "T2"
            elif unrealPct >= p.mama_trail_min_pct:
                if isLong and bar.close < mama:
                    mamaTrailExit = True
                    mamaTrailTier = "T1"
                if isShort and bar.close > mama:
                    mamaTrailExit = True
                    mamaTrailTier = "T1"

        # Time-based profit lock
        profitLockExit = False
        if p.use_profit_lock and barsHeld >= p.lock_bars and self._tradeMaxPct >= p.lock_min_pct:
            lockFloor = self._tradeMaxPct * (p.lock_retrace_pct / 100.0)
            if unrealPct < lockFloor:
                profitLockExit = True

        # Standard MAMA/FAMA exit
        standardExitLong = (i > 0 and self.mamaCrossDown[i]) or (mama < fama and diffOk)
        standardExitShort = (i > 0 and self.mamaCrossUp[i]) or (mama > fama and diffOk)

        holdExpiredAgainstLong = holdTimeMet and isLong and mama < fama
        holdExpiredAgainstShort = holdTimeMet and isShort and mama > fama

        # Combined exit priority
        exitLongSignal = False
        exitShortSignal = False
        exitReason = ""

        if isLong:
            if beStopHit:
                exitLongSignal = True
                exitReason = "BE_STOP"
            elif holdTimeMet and mamaTrailExit:
                exitLongSignal = True
                exitReason = "MAMA_" + mamaTrailTier
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
                exitReason = "MAMA_" + mamaTrailTier
            elif profitLockExit:
                exitShortSignal = True
                exitReason = "PROFIT_LOCK"
            elif holdTimeMet and standardExitShort:
                exitShortSignal = True
                exitReason = "CROSS_EXIT"
            elif holdExpiredAgainstShort:
                exitShortSignal = True
                exitReason = "CROSS_EXIT"

        # Reversal signals
        canReverseLong = holdTimeMet and shortSignalChanged and wantShort and canTrade
        canReverseShort = holdTimeMet and longSignalChanged and wantLong and canTrade

        # Trading Logic
        canActThisBar = ctx.bar_index != self._lastActionBar
        reversedThisBar = False
        exitedThisBar = False
        reentryMinBars = 6

        if canActThisBar:
            # CASE 1: REVERSAL Long → Short
            if p.use_swing_logic and isLong and canReverseLong and not beStopHit:
                ctx.close_all(comment="REVERSAL")
                ctx.entry("Short", "short", qty=qtyContracts, qty_func=self._size_at_fill)
                reversedThisBar = True
                self._lastActionBar = ctx.bar_index
                self._pendingShort = False
                if p.show_alert_labels and ctx.bar_index != self._lastLabelBar:
                    self._lastLabelBar = ctx.bar_index

            # CASE 2: REVERSAL Short → Long
            elif p.use_swing_logic and isShort and canReverseShort and not beStopHit:
                ctx.close_all(comment="REVERSAL")
                ctx.entry("Long", "long", qty=qtyContracts, qty_func=self._size_at_fill)
                reversedThisBar = True
                self._lastActionBar = ctx.bar_index
                self._pendingLong = False
                if p.show_alert_labels and ctx.bar_index != self._lastLabelBar:
                    self._lastLabelBar = ctx.bar_index

            # CASE 3: EXIT Long
            elif isLong and exitLongSignal and not reversedThisBar:
                canReverseOnExit = p.use_swing_logic and exitReason == "CROSS_EXIT" and mama < fama and wantShort and canTrade
                if canReverseOnExit:
                    ctx.close_all(comment=exitReason)
                    ctx.entry("Short", "short", qty=qtyContracts, qty_func=self._size_at_fill)
                    reversedThisBar = True
                    self._lastActionBar = ctx.bar_index
                    if p.show_alert_labels and ctx.bar_index != self._lastLabelBar:
                        self._lastLabelBar = ctx.bar_index
                else:
                    ctx.close("Long", comment=exitReason)
                    exitedThisBar = True
                    self._lastActionBar = ctx.bar_index
                    self._lastExitBar = ctx.bar_index
                    self._lastExitDir = 1
                    exitLabel = ("BE EXIT" if exitReason == "BE_STOP" else
                               "TRAIL T3 EXIT" if exitReason == "MAMA_T3" else  
                               "TRAIL T2 EXIT" if exitReason == "MAMA_T2" else
                               "TRAIL T1 EXIT" if exitReason == "MAMA_T1" else
                               "PROFIT LOCK EXIT" if exitReason == "PROFIT_LOCK" else "EXIT")
                    if p.show_alert_labels and ctx.bar_index != self._lastLabelBar:
                        self._lastLabelBar = ctx.bar_index

            # CASE 4: EXIT Short
            elif isShort and exitShortSignal and not reversedThisBar:
                canReverseOnExitS = p.use_swing_logic and exitReason == "CROSS_EXIT" and mama > fama and wantLong and canTrade
                if canReverseOnExitS:
                    ctx.close_all(comment=exitReason)
                    ctx.entry("Long", "long", qty=qtyContracts, qty_func=self._size_at_fill)
                    reversedThisBar = True
                    self._lastActionBar = ctx.bar_index
                    if p.show_alert_labels and ctx.bar_index != self._lastLabelBar:
                        self._lastLabelBar = ctx.bar_index
                else:
                    ctx.close("Short", comment=exitReason)
                    exitedThisBar = True
                    self._lastActionBar = ctx.bar_index
                    self._lastExitBar = ctx.bar_index
                    self._lastExitDir = -1
                    exitLabel = ("BE EXIT" if exitReason == "BE_STOP" else
                               "TRAIL T3 EXIT" if exitReason == "MAMA_T3" else
                               "TRAIL T2 EXIT" if exitReason == "MAMA_T2" else
                               "TRAIL T1 EXIT" if exitReason == "MAMA_T1" else
                               "PROFIT LOCK EXIT" if exitReason == "PROFIT_LOCK" else "EXIT")
                    if p.show_alert_labels and ctx.bar_index != self._lastLabelBar:
                        self._lastLabelBar = ctx.bar_index

            # CASE 5: Non-swing exits
            elif not p.use_swing_logic:
                if isLong and mama < fama and holdTimeMet:
                    ctx.close("Long", comment="CROSS_EXIT")
                    exitedThisBar = True
                    self._lastActionBar = ctx.bar_index
                if isShort and mama > fama and holdTimeMet:
                    ctx.close("Short", comment="CROSS_EXIT")
                    exitedThisBar = True
                    self._lastActionBar = ctx.bar_index

            # CASE 6: FRESH ENTRY from flat
            longCooldown = self._lastExitDir == 1 and (ctx.bar_index - self._lastExitBar) < reentryMinBars
            shortCooldown = self._lastExitDir == -1 and (ctx.bar_index - self._lastExitBar) < reentryMinBars

            if canTrade and not reversedThisBar and isFlat and hourOk:
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

        # Flip handling cooldown
        tfSeconds = 300  # Assuming 5m timeframe
        tfm = tfSeconds / 60.0
        autoBlock = tfm < 23
        blockSameBarFlip = p.flip_mode == "Disallow same-bar flip" or (p.flip_mode == "Auto (<23m disallow)" and autoBlock)
        
        if (reversedThisBar or exitedThisBar) and blockSameBarFlip:
            self._cooldownBars = 1
            
        if self._cooldownBars > 0:
            self._cooldownBars -= 1

        # Track position for next bar's new-entry detection
        self._prev_pos = ctx.position_size