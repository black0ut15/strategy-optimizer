"""Pure ORB (Opening Range Breakout) Strategy.

Translated from Pine Script v6: "Pure ORB [MES 3ct]"
Session-based intraday strategy for futures (MES, ES, NQ, etc.)

Logic:
1. Build opening range (OR) during first N minutes of session
2. Break above OR high → long, break below OR low → short
3. Stop loss at opposite side of OR (or midpoint, or ATR-based)
4. Take profit at R multiple (or OR multiple, or ATR multiple)
5. Trailing stop activates after reaching R threshold
6. Breakeven stop moves SL to entry after reaching R threshold
7. Time stop closes unprofitable trades after N bars
8. EOD flatten closes all positions before session end
9. Max trades per day limit

Session times are in exchange timezone (CT for CME products).
Data timestamps must be UTC.

Fee model: This strategy uses fixed $ per contract (futures).
The engine's percentage-based fee is set to approximate this.
For MES at ~5800: $0.50 commission + $0.25 slippage = $0.75/side/contract
3 contracts = $2.25/side. Notional ~$87,000. Fee% ≈ 0.0026%.
"""

import numpy as np
from datetime import datetime, timezone, timedelta
from strategy_base import Strategy, Param
import ta


# ═══════════════════════════════════════════════════════════════════════════
# DST-aware timezone conversion
# ═══════════════════════════════════════════════════════════════════════════

def _us_dst_ranges(year):
    """Return (dst_start_utc, dst_end_utc) for US DST in given year.
    DST: 2nd Sunday March 2:00 AM local → 1st Sunday November 2:00 AM local.
    """
    # 2nd Sunday of March
    mar1 = datetime(year, 3, 1)
    # days until Sunday (6 = Sunday in weekday())
    days_to_sun = (6 - mar1.weekday()) % 7
    first_sun = mar1 + timedelta(days=days_to_sun)
    second_sun = first_sun + timedelta(days=7)
    dst_start = datetime(year, 3, second_sun.day, 8, 0)  # 2AM CT = 8AM UTC

    # 1st Sunday of November
    nov1 = datetime(year, 11, 1)
    days_to_sun = (6 - nov1.weekday()) % 7
    first_sun_nov = nov1 + timedelta(days=days_to_sun)
    if first_sun_nov.day == 1 and nov1.weekday() != 6:
        first_sun_nov = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    dst_end = datetime(year, 11, first_sun_nov.day, 7, 0)  # 2AM CT(CDT) = 7AM UTC

    return dst_start, dst_end


def _utc_to_ct_minutes(ts_ms, dst_cache={}):
    """Convert UTC timestamp (ms) to Central Time minutes-since-midnight.
    Returns (ct_minutes, ct_date_str, is_dst).
    CT = UTC-6 (CST) or UTC-5 (CDT during DST).
    """
    dt = datetime.utcfromtimestamp(ts_ms / 1000)
    year = dt.year
    if year not in dst_cache:
        dst_cache[year] = _us_dst_ranges(year)
    dst_start, dst_end = dst_cache[year]

    is_dst = dst_start <= dt < dst_end
    offset_hours = 5 if is_dst else 6
    ct_dt = dt - timedelta(hours=offset_hours)

    ct_minutes = ct_dt.hour * 60 + ct_dt.minute
    ct_date = ct_dt.strftime('%Y-%m-%d')
    return ct_minutes, ct_date, is_dst


def _parse_session(session_str):
    """Parse 'HHMM-HHMM' to (start_minutes, end_minutes)."""
    parts = session_str.split('-')
    start = int(parts[0][:2]) * 60 + int(parts[0][2:])
    end = int(parts[1][:2]) * 60 + int(parts[1][2:])
    return start, end


def _in_session(ct_minutes, session_start, session_end):
    """Check if ct_minutes falls within session window."""
    if session_start <= session_end:
        return session_start <= ct_minutes < session_end
    else:  # wraps midnight
        return ct_minutes >= session_start or ct_minutes < session_end


class PureORB(Strategy):
    """Pure Opening Range Breakout strategy for futures."""

    params = {
        # Strategy
        "direction": Param("Both", options=["Both", "Long Only", "Short Only"], group="Strategy"),
        "max_trades_per_day": Param(1, min=1, max=5, step=1, group="Strategy"),
        "contracts": Param(3, min=1, max=20, step=1, group="Strategy"),
        "confirm_close": Param(True, group="Strategy",
            tooltip="Require candle close beyond OR level (vs high/low wick)"),

        # Session
        "or_minutes": Param(15, min=1, max=120, step=5, group="Session",
            tooltip="Duration to build the opening range"),
        "cash_session": Param("0830-1500", group="Session",
            tooltip="Trading session in exchange TZ (CT for CME)"),
        "flatten_session": Param("1340-1345", group="Session",
            tooltip="Flatten all positions during this window"),
        "entry_session": Param("0830-1100", group="Session",
            tooltip="Only enter new trades during this window"),

        # Stop Loss
        "sl_mode": Param("Opposite Side",
            options=["Opposite Side", "OR Midpoint", "ATR"], group="Stop Loss"),
        "sl_pad": Param(0.0, min=0.0, max=50.0, step=0.25, group="Stop Loss",
            tooltip="Extra buffer beyond stop level (points)"),
        "sl_atr_mult": Param(1.5, min=0.5, max=5.0, step=0.1, group="Stop Loss"),

        # Take Profit
        "tp_mode": Param("R Multiple",
            options=["R Multiple", "OR Multiple", "ATR Multiple", "None"], group="Take Profit"),
        "tp_r_mult": Param(2.5, min=0.5, max=10.0, step=0.25, group="Take Profit"),
        "tp_or_mult": Param(2.0, min=0.5, max=10.0, step=0.25, group="Take Profit"),
        "tp_atr_mult": Param(3.0, min=0.5, max=10.0, step=0.5, group="Take Profit"),

        # Trailing Stop
        "use_trail": Param(True, group="Trailing Stop"),
        "trail_atr_mult": Param(1.2, min=0.3, max=5.0, step=0.1, group="Trailing Stop"),
        "trail_after_r": Param(0.75, min=0.1, max=5.0, step=0.25, group="Trailing Stop"),

        # Breakeven
        "use_be": Param(True, group="Breakeven"),
        "be_after_r": Param(0.5, min=0.1, max=3.0, step=0.1, group="Breakeven"),

        # Time Stop
        "use_time_stop": Param(True, group="Time Stop"),
        "time_stop_bars": Param(12, min=1, max=100, step=1, group="Time Stop",
            tooltip="Close if at or below entry after this many bars"),

        # ATR
        "atr_length": Param(14, min=5, max=50, step=1, group="ATR"),

        # Point value (for futures P&L calculation)
        "point_value": Param(5.0, group="Instrument",
            tooltip="Dollar value per point. MES=5, ES=50, NQ=20, MNQ=2"),
    }

    def init(self, ctx):
        """Pre-compute ATR over all bars."""
        self.atr_arr = ta.atr(ctx.bars.high, ctx.bars.low, ctx.bars.close,
                              self.p.atr_length)

        # Pre-compute CT times for every bar (cached on Bars to avoid
        # recomputing across sweep iterations)
        if not hasattr(ctx.bars, '_ct_minutes'):
            n = len(ctx.bars)
            ct_minutes = np.zeros(n, dtype=int)
            ct_dates = [''] * n
            for i in range(n):
                ct_min, ct_date, _ = _utc_to_ct_minutes(int(ctx.bars.timestamp[i]))
                ct_minutes[i] = ct_min
                ct_dates[i] = ct_date
            ctx.bars._ct_minutes = ct_minutes
            ctx.bars._ct_dates = ct_dates

        n = len(ctx.bars)
        self.ct_minutes = ctx.bars._ct_minutes
        self.ct_dates = ctx.bars._ct_dates

        # Parse session windows
        self.session_start, self.session_end = _parse_session(self.p.cash_session)
        self.flatten_start, self.flatten_end = _parse_session(self.p.flatten_session)
        self.entry_start, self.entry_end = _parse_session(self.p.entry_session)

        # Session state variables
        self.or_high = float('nan')
        self.or_low = float('nan')
        self.or_mid = float('nan')
        self.or_locked = False
        self.or_range = float('nan')
        self.or_bar_count = 0
        self.prev_in_session = False
        self.prev_ct_date = ''

        # Trade management state
        self.entry_bar = 0
        self.entry_price = float('nan')
        self.initial_sl = float('nan')
        self.initial_risk = float('nan')
        self.initial_tp = float('nan')
        self.trail_active = False
        self.trail_stop_price = float('nan')
        self.trades_today = 0

        # Track whether we have an active position (since ctx doesn't expose _direction)
        self.in_long = False
        self.in_short = False
        self.is_flat = True

        # Breakeven applied flag (don't re-apply every bar)
        self.be_applied = False

    def on_bar(self, bar, ctx):
        """Per-bar ORB logic."""
        i = ctx.bar_index
        ct_min = self.ct_minutes[i]
        ct_date = self.ct_dates[i]

        # Session detection
        in_session = _in_session(ct_min, self.session_start, self.session_end)
        is_flatten_time = _in_session(ct_min, self.flatten_start, self.flatten_end)
        in_entry_window = _in_session(ct_min, self.entry_start, self.entry_end)

        is_session_first_bar = in_session and not self.prev_in_session
        is_new_day = ct_date != self.prev_ct_date

        self.prev_in_session = in_session
        self.prev_ct_date = ct_date

        # Reset daily counter
        if is_new_day:
            self.trades_today = 0

        # Track position state from context
        pos = ctx.position_size
        self.in_long = pos > 0
        self.in_short = pos < 0
        self.is_flat = pos == 0

        # ═══════════════════════════════════════════════════════
        # Opening Range construction
        # ═══════════════════════════════════════════════════════
        bar_minutes = 5  # 5-minute bars

        if is_session_first_bar:
            self.or_high = bar.high
            self.or_low = bar.low
            self.or_locked = False
            self.or_range = float('nan')
            self.or_mid = float('nan')
            self.or_bar_count = 1
        elif in_session and not self.or_locked:
            self.or_bar_count += 1
            self.or_high = max(self.or_high, bar.high) if not np.isnan(self.or_high) else bar.high
            self.or_low = min(self.or_low, bar.low) if not np.isnan(self.or_low) else bar.low

        if in_session and not self.or_locked and self.or_bar_count * bar_minutes >= self.p.or_minutes:
            self.or_locked = True
            self.or_range = self.or_high - self.or_low
            self.or_mid = (self.or_high + self.or_low) / 2.0

        # Entry levels
        long_entry = self.or_high
        short_entry = self.or_low

        atr_val = self.atr_arr[i] if not np.isnan(self.atr_arr[i]) else 0.0

        # ═══════════════════════════════════════════════════════
        # Trade management (while in position)
        # ═══════════════════════════════════════════════════════
        if not self.is_flat:
            bars_since_entry = i - self.entry_bar
            current_r = 0.0
            if self.initial_risk > 0:
                if self.in_long:
                    current_r = (bar.close - self.entry_price) / self.initial_risk
                elif self.in_short:
                    current_r = (self.entry_price - bar.close) / self.initial_risk

            # Breakeven stop
            if self.p.use_be and self.initial_risk > 0 and current_r >= self.p.be_after_r and not self.be_applied:
                be_sl = self.entry_price + (self.p.sl_pad if self.in_long else -self.p.sl_pad)
                if self.in_long:
                    ctx.exit("Long Exit", "Long", stop=be_sl,
                             limit=self.initial_tp if not np.isnan(self.initial_tp) else None)
                elif self.in_short:
                    ctx.exit("Short Exit", "Short", stop=be_sl,
                             limit=self.initial_tp if not np.isnan(self.initial_tp) else None)
                self.be_applied = True

            # Trailing stop activation
            if self.p.use_trail and self.initial_risk > 0 and current_r >= self.p.trail_after_r:
                self.trail_active = True

            # Trailing stop update
            if self.trail_active:
                trail_offset = atr_val * self.p.trail_atr_mult
                if self.in_long:
                    new_trail = bar.close - trail_offset
                    if np.isnan(self.trail_stop_price):
                        self.trail_stop_price = new_trail
                    else:
                        self.trail_stop_price = max(self.trail_stop_price, new_trail)
                    # Only apply if trail is above entry (better than breakeven)
                    if self.trail_stop_price > self.entry_price:
                        ctx.exit("Long Exit", "Long", stop=self.trail_stop_price,
                                 limit=self.initial_tp if not np.isnan(self.initial_tp) else None)
                elif self.in_short:
                    new_trail = bar.close + trail_offset
                    if np.isnan(self.trail_stop_price):
                        self.trail_stop_price = new_trail
                    else:
                        self.trail_stop_price = min(self.trail_stop_price, new_trail)
                    if self.trail_stop_price < self.entry_price:
                        ctx.exit("Short Exit", "Short", stop=self.trail_stop_price,
                                 limit=self.initial_tp if not np.isnan(self.initial_tp) else None)

            # Time stop
            if self.p.use_time_stop and bars_since_entry >= self.p.time_stop_bars:
                if self.in_long and bar.close <= self.entry_price:
                    ctx.set_exit_reason("Time Stop")
                    ctx.close("Long", comment="Time Stop")
                elif self.in_short and bar.close >= self.entry_price:
                    ctx.set_exit_reason("Time Stop")
                    ctx.close("Short", comment="Time Stop")

            # EOD Flatten
            if is_flatten_time:
                ctx.set_exit_reason("EOD Flatten")
                ctx.close_all(comment="EOD Flatten")

        # ═══════════════════════════════════════════════════════
        # Reset when flat
        # ═══════════════════════════════════════════════════════
        if self.is_flat:
            self.trail_active = False
            self.trail_stop_price = float('nan')

        # ═══════════════════════════════════════════════════════
        # Entry logic
        # ═══════════════════════════════════════════════════════
        can_trade = (self.or_locked and in_session and in_entry_window
                     and not is_flatten_time and self.is_flat
                     and self.trades_today < self.p.max_trades_per_day)

        can_go_long = self.p.direction in ("Both", "Long Only")
        can_go_short = self.p.direction in ("Both", "Short Only")

        if self.p.confirm_close:
            long_trigger = bar.close > long_entry
            short_trigger = bar.close < short_entry
        else:
            long_trigger = bar.high > long_entry
            short_trigger = bar.low < short_entry

        # Long entry
        if (can_trade and can_go_long and not np.isnan(long_entry)
                and not np.isnan(self.or_low) and long_trigger):
            sl = self._calc_long_sl(long_entry, atr_val)
            risk = abs(bar.close - sl)
            tp = self._calc_long_tp(bar.close, risk, atr_val)

            qty = self.p.contracts * self.p.point_value
            ctx.entry("Long", "long", qty=qty)

            # Set bracket exit
            if not np.isnan(tp):
                ctx.exit("Long Exit", "Long", stop=sl, limit=tp)
            else:
                ctx.exit("Long Exit", "Long", stop=sl)

            self.entry_bar = i
            self.entry_price = bar.close
            self.initial_sl = sl
            self.initial_risk = risk
            self.initial_tp = tp
            self.trail_active = False
            self.trail_stop_price = float('nan')
            self.be_applied = False
            self.trades_today += 1

        # Short entry
        elif (can_trade and can_go_short and not np.isnan(short_entry)
              and not np.isnan(self.or_high) and short_trigger):
            sl = self._calc_short_sl(short_entry, atr_val)
            risk = abs(sl - bar.close)
            tp = self._calc_short_tp(bar.close, risk, atr_val)

            qty = self.p.contracts * self.p.point_value
            ctx.entry("Short", "short", qty=qty)

            if not np.isnan(tp):
                ctx.exit("Short Exit", "Short", stop=sl, limit=tp)
            else:
                ctx.exit("Short Exit", "Short", stop=sl)

            self.entry_bar = i
            self.entry_price = bar.close
            self.initial_sl = sl
            self.initial_risk = risk
            self.initial_tp = tp
            self.trail_active = False
            self.trail_stop_price = float('nan')
            self.be_applied = False
            self.trades_today += 1

    def _calc_long_sl(self, entry, atr_val):
        """Calculate long stop loss price."""
        mode = self.p.sl_mode
        if mode == "Opposite Side":
            return self.or_low - self.p.sl_pad
        elif mode == "OR Midpoint":
            return self.or_mid - self.p.sl_pad
        else:  # ATR
            return entry - atr_val * self.p.sl_atr_mult

    def _calc_short_sl(self, entry, atr_val):
        """Calculate short stop loss price."""
        mode = self.p.sl_mode
        if mode == "Opposite Side":
            return self.or_high + self.p.sl_pad
        elif mode == "OR Midpoint":
            return self.or_mid + self.p.sl_pad
        else:  # ATR
            return entry + atr_val * self.p.sl_atr_mult

    def _calc_long_tp(self, entry, risk, atr_val):
        """Calculate long take profit price."""
        mode = self.p.tp_mode
        if mode == "R Multiple":
            return entry + risk * self.p.tp_r_mult
        elif mode == "OR Multiple":
            r = self.or_range if not np.isnan(self.or_range) else risk
            return entry + r * self.p.tp_or_mult
        elif mode == "ATR Multiple":
            return entry + atr_val * self.p.tp_atr_mult
        return float('nan')  # None mode

    def _calc_short_tp(self, entry, risk, atr_val):
        """Calculate short take profit price."""
        mode = self.p.tp_mode
        if mode == "R Multiple":
            return entry - risk * self.p.tp_r_mult
        elif mode == "OR Multiple":
            r = self.or_range if not np.isnan(self.or_range) else risk
            return entry - r * self.p.tp_or_mult
        elif mode == "ATR Multiple":
            return entry - atr_val * self.p.tp_atr_mult
        return float('nan')  # None mode
