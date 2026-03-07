"""Backtester Engine — runs strategies bar-by-bar.

Two modes:
  - fast: returns summary stats only (for parameter sweeps)
  - detail: returns summary + full trade log + equity curve

Order fill model matches TradingView:
  - process_orders_on_close=false: entries fill at current bar close
  - Reversals: close existing + open new at same bar close
  - Fees: per-side percentage on notional
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from strategy_base import Strategy, ParamAccessor


@dataclass
class BarData:
    """Single bar of OHLCV data."""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Bars:
    """Full bar arrays for vectorized indicator computation in init()."""
    timestamp: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    hl2: np.ndarray

    @staticmethod
    def from_csv(filepath: str) -> "Bars":
        """Load OHLCV data from CSV file."""
        data = np.genfromtxt(
            filepath, delimiter=",", skip_header=1,
            dtype=float, filling_values=np.nan
        )
        return Bars(
            timestamp=data[:, 0].astype(np.int64),
            open=data[:, 1],
            high=data[:, 2],
            low=data[:, 3],
            close=data[:, 4],
            volume=data[:, 5],
            hl2=(data[:, 2] + data[:, 3]) / 2.0,
        )

    def __len__(self) -> int:
        return len(self.close)


@dataclass
class Trade:
    """Completed trade record."""
    trade_num: int
    direction: str  # "long" or "short"
    entry_bar: int
    exit_bar: int
    entry_price: float
    exit_price: float
    qty: float
    gross_pnl: float
    fee: float
    net_pnl: float
    bars_held: int
    exit_reason: str = ""
    entry_time: int = 0
    exit_time: int = 0


@dataclass
class SummaryStats:
    """Backtest summary statistics."""
    net_profit: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0


@dataclass
class DetailedResult:
    """Full backtest result with trade log and equity curve."""
    stats: SummaryStats
    trades: List[Trade]
    equity_curve: np.ndarray


class Context:
    """Trading context passed to strategy during bar-by-bar execution.

    The strategy uses this to:
    - Read current position state (position_size, equity, bar_index)
    - Submit orders (entry, close, close_all)
    - Access pre-loaded bar arrays for indicator computation
    """

    def __init__(self, bars: Bars, initial_capital: float, fee_pct: float, detail: bool = False):
        self.bars = bars
        self.initial_capital = initial_capital
        self.fee_pct = fee_pct  # per-side fee as percentage (e.g. 0.035 = 0.035%)
        self.detail = detail

        # Position state
        self._direction = 0  # 0=flat, 1=long, -1=short
        self._qty = 0.0
        self._entry_price = 0.0
        self._entry_bar = 0
        self._entry_time = 0

        # Account state
        self.equity = initial_capital
        self._peak_equity = initial_capital
        self._max_drawdown = 0.0

        # Stats accumulators
        self._total_trades = 0
        self._winning_trades = 0
        self._gross_profit = 0.0
        self._gross_loss = 0.0

        # Current bar
        self.bar_index = 0
        self._last_close = 0.0  # Tracks last bar close for strategy_equity

        # Pending orders (filled during _process_orders)
        self._pending_entries: List[Tuple[str, str, float]] = []  # (id, direction, qty)
        self._pending_close: bool = False
        self._pending_close_all: bool = False
        self._pending_close_comment: str = ""

        # Bracket exit orders (stop/limit) — set via strategy.exit()
        # These persist until the position is closed or replaced
        self._exit_stop: Optional[float] = None   # Stop loss price
        self._exit_limit: Optional[float] = None   # Take profit price
        self._exit_id: str = ""                     # Exit order name

        # Detail mode storage
        self._trades: List[Trade] = []
        self._equity_curve: List[float] = []
        self._exit_reason: str = ""

    @property
    def position_size(self) -> float:
        """Current position size. Positive=long, negative=short, 0=flat."""
        if self._direction == 1:
            return self._qty
        elif self._direction == -1:
            return -self._qty
        return 0.0

    @property
    def position_avg_price(self) -> float:
        """Average entry price of current position."""
        return self._entry_price if self._direction != 0 else 0.0

    @property
    def strategy_equity(self) -> float:
        """Equity including unrealized P&L — matches Pine's strategy.equity.

        Pine Script's strategy.equity = realized equity + open position P&L.
        Pine evaluates this at bar close, so we use the current bar's close.
        """
        if self._direction != 0:
            # Use current bar's close for unrealized calculation
            current_close = self.bars.close[self.bar_index]
            return self.equity + self._unrealized_pnl(current_close)
        return self.equity

    def entry(self, id: str, direction: str, qty: float = 1.0) -> None:
        """Queue an entry order. Filled at current bar's close."""
        self._pending_entries.append((id, direction.lower(), qty))

    def close(self, id: str, comment: str = "") -> None:
        """Queue a close order for the named position."""
        self._pending_close = True
        self._pending_close_comment = comment

    def close_all(self, comment: str = "") -> None:
        """Queue close of all positions."""
        self._pending_close_all = True
        self._pending_close_comment = comment

    def exit(self, id: str, from_entry: str = "",
             stop: Optional[float] = None,
             limit: Optional[float] = None) -> None:
        """Set bracket exit orders (stop loss and/or take profit).

        Matches Pine's strategy.exit(). These orders persist until:
        - The position is closed (by stop, limit, or manual close)
        - New exit() call replaces them
        - Position is flat

        Args:
            id: Exit order identifier
            from_entry: Entry order to attach to (informational)
            stop: Stop loss price (exit if price goes against position)
            limit: Take profit price (exit if price goes in favor)
        """
        self._exit_stop = stop
        self._exit_limit = limit
        self._exit_id = id

    def set_exit_reason(self, reason: str) -> None:
        """Set exit reason for the current bar's trade (detail mode)."""
        self._exit_reason = reason

    def _process_orders(self, bar: BarData) -> None:
        """Process bracket exits and track equity at bar close.

        With process_orders_on_close=false (TradingView default):
        Entry orders placed on bar N fill at bar N+1's open price.
        But bracket exits (stop/limit) are checked intra-bar against high/low.
        """
        self._last_close = bar.close

        # Check bracket exit orders (stop/limit) intra-bar
        if self._direction != 0 and (self._exit_stop is not None or self._exit_limit is not None):
            self._check_bracket_exits(bar)

        # Update equity with unrealized PnL for drawdown tracking
        if self._direction != 0:
            unreal = self._unrealized_pnl(bar.close)
            current_equity = self.equity + unreal
            if current_equity > self._peak_equity:
                self._peak_equity = current_equity
            dd = self._peak_equity - current_equity
            if dd > self._max_drawdown:
                self._max_drawdown = dd

    def _check_bracket_exits(self, bar: BarData) -> None:
        """Check if stop loss or take profit was hit during this bar.

        TradingView fill logic for stops/limits:
        - For longs: stop triggers if low <= stop price, limit triggers if high >= limit price
        - For shorts: stop triggers if high >= stop price, limit triggers if low <= limit price
        - If both could trigger on same bar, stop takes priority (conservative assumption)
        - Fill price = the stop/limit price itself (not bar open/close)
        """
        stop = self._exit_stop
        limit = self._exit_limit

        stop_hit = False
        limit_hit = False

        if self._direction == 1:  # Long position
            if stop is not None and bar.low <= stop:
                stop_hit = True
            if limit is not None and bar.high >= limit:
                limit_hit = True
        elif self._direction == -1:  # Short position
            if stop is not None and bar.high >= stop:
                stop_hit = True
            if limit is not None and bar.low <= limit:
                limit_hit = True

        # Stop takes priority if both hit on same bar
        if stop_hit:
            self._exit_reason = "STOP"
            self._close_position(stop, bar)
            self._clear_bracket_exits()
        elif limit_hit:
            self._exit_reason = "TAKE_PROFIT"
            self._close_position(limit, bar)
            self._clear_bracket_exits()

    def _clear_bracket_exits(self) -> None:
        """Clear all bracket exit orders."""
        self._exit_stop = None
        self._exit_limit = None
        self._exit_id = ""

    def _fill_pending_orders(self, bar: BarData) -> None:
        """Fill queued orders at this bar's open. Called at start of each bar."""
        fill_price = bar.open

        # 1. Process close/close_all
        if (self._pending_close or self._pending_close_all) and self._direction != 0:
            if self._pending_close_comment:
                self._exit_reason = self._pending_close_comment
            self._close_position(fill_price, bar)

        self._pending_close = False
        self._pending_close_all = False
        self._pending_close_comment = ""

        # 2. Process entries
        for entry_id, direction, qty in self._pending_entries:
            dir_int = 1 if direction == "long" else -1

            # Reversal: close existing first
            if self._direction != 0 and self._direction != dir_int:
                self._close_position(fill_price, bar)

            # Open new position
            if self._direction == 0:
                self._direction = dir_int
                self._qty = qty
                self._entry_price = fill_price
                self._entry_bar = self.bar_index
                self._entry_time = bar.timestamp

        self._pending_entries.clear()
        self._exit_reason = ""

    def _close_position(self, exit_price: float, bar: BarData) -> None:
        """Close current position at given price, record trade."""
        if self._direction == 0:
            return

        # P&L calculation
        if self._direction == 1:
            gross_pnl = (exit_price - self._entry_price) * self._qty
        else:
            gross_pnl = (self._entry_price - exit_price) * self._qty

        # Fees: per-side on notional
        entry_notional = self._entry_price * self._qty
        exit_notional = exit_price * self._qty
        fee = (entry_notional + exit_notional) * (self.fee_pct / 100.0)
        net_pnl = gross_pnl - fee

        # Update equity
        self.equity += net_pnl
        self._total_trades += 1

        if net_pnl > 0:
            self._winning_trades += 1
            self._gross_profit += net_pnl
        else:
            self._gross_loss += abs(net_pnl)

        # Peak/drawdown
        if self.equity > self._peak_equity:
            self._peak_equity = self.equity
        dd = self._peak_equity - self.equity
        if dd > self._max_drawdown:
            self._max_drawdown = dd

        # Record trade (detail mode)
        if self.detail:
            self._trades.append(Trade(
                trade_num=self._total_trades,
                direction="long" if self._direction == 1 else "short",
                entry_bar=self._entry_bar,
                exit_bar=self.bar_index,
                entry_price=self._entry_price,
                exit_price=exit_price,
                qty=self._qty,
                gross_pnl=gross_pnl,
                fee=fee,
                net_pnl=net_pnl,
                bars_held=self.bar_index - self._entry_bar,
                exit_reason=self._exit_reason,
                entry_time=self._entry_time,
                exit_time=bar.timestamp,
            ))

        # Reset position
        self._direction = 0
        self._qty = 0.0
        self._entry_price = 0.0
        self._entry_bar = 0
        self._clear_bracket_exits()

    def _unrealized_pnl(self, current_price: float) -> float:
        """Calculate unrealized P&L for current position."""
        if self._direction == 1:
            return (current_price - self._entry_price) * self._qty
        elif self._direction == -1:
            return (self._entry_price - current_price) * self._qty
        return 0.0

    def _build_stats(self) -> SummaryStats:
        """Build summary statistics from accumulated data."""
        pf = (self._gross_profit / self._gross_loss
              if self._gross_loss > 0
              else 999.0 if self._gross_profit > 0
              else 0.0)

        wr = (self._winning_trades / self._total_trades * 100.0
              if self._total_trades > 0 else 0.0)

        dd_pct = (self._max_drawdown / self._peak_equity * 100.0
                  if self._peak_equity > 0 else 0.0)

        return SummaryStats(
            net_profit=self.equity - self.initial_capital,
            gross_profit=self._gross_profit,
            gross_loss=self._gross_loss,
            profit_factor=pf,
            max_drawdown=self._max_drawdown,
            max_drawdown_pct=dd_pct,
            win_rate=wr,
            total_trades=self._total_trades,
            winning_trades=self._winning_trades,
            losing_trades=self._total_trades - self._winning_trades,
        )


class BacktestEngine:
    """Runs a strategy against historical data.

    Usage:
        bars = Bars.from_csv("BTCUSDT_5m_365d_bt.csv")
        strategy = MesaMamaFama()
        params = {"fast_limit": 0.38, "slow_limit": 0.035}

        # Fast mode (sweeps)
        stats = BacktestEngine.run_fast(strategy, bars, params,
                                        initial_capital=1_000_000, fee_pct=0.035)

        # Detail mode (drill-down)
        result = BacktestEngine.run_detail(strategy, bars, params,
                                            initial_capital=1_000_000, fee_pct=0.035)
    """

    @staticmethod
    def run_fast(
        strategy: Strategy,
        bars: Bars,
        params: Dict[str, Any],
        initial_capital: float = 1_000_000.0,
        fee_pct: float = 0.035,
        warmup_bars: int = 0,
    ) -> SummaryStats:
        """Run backtest in fast mode — returns summary stats only.

        Args:
            warmup_bars: Number of initial bars to skip for indicator warmup.
                         TradingView loads extra bars before the visible range,
                         so set this to match the indicator convergence period.
        """
        ctx = Context(bars, initial_capital, fee_pct, detail=False)
        return BacktestEngine._run(strategy, bars, params, ctx, warmup_bars)

    @staticmethod
    def run_detail(
        strategy: Strategy,
        bars: Bars,
        params: Dict[str, Any],
        initial_capital: float = 1_000_000.0,
        fee_pct: float = 0.035,
        warmup_bars: int = 0,
    ) -> DetailedResult:
        """Run backtest in detail mode — returns stats + trades + equity curve.

        Args:
            warmup_bars: Number of initial bars to skip for indicator warmup.
        """
        ctx = Context(bars, initial_capital, fee_pct, detail=True)
        stats = BacktestEngine._run(strategy, bars, params, ctx, warmup_bars)
        return DetailedResult(
            stats=stats,
            trades=ctx._trades,
            equity_curve=np.array(ctx._equity_curve),
        )

    @staticmethod
    def _run(
        strategy: Strategy,
        bars: Bars,
        params: Dict[str, Any],
        ctx: Context,
        warmup_bars: int = 0,
    ) -> SummaryStats:
        """Core bar-by-bar execution loop."""
        # Set parameters on strategy
        merged = strategy.get_param_defaults()
        merged.update(params)
        strategy.p = ParamAccessor(merged)

        # Init: pre-compute indicators (uses ALL bars, including warmup)
        strategy.init(ctx)

        n = len(bars)

        # Bar-by-bar loop
        for i in range(n):
            ctx.bar_index = i

            bar = BarData(
                timestamp=int(bars.timestamp[i]),
                open=bars.open[i],
                high=bars.high[i],
                low=bars.low[i],
                close=bars.close[i],
                volume=bars.volume[i],
            )

            # Fill pending orders from previous bar at this bar's open
            ctx._fill_pending_orders(bar)

            # Run strategy logic (skip warmup bars for trading, but
            # indicators are already computed over all bars in init())
            if i >= warmup_bars:
                strategy.on_bar(bar, ctx)

            # Track equity/drawdown at bar close
            ctx._process_orders(bar)

            # Record equity (detail mode)
            if ctx.detail:
                unreal = ctx._unrealized_pnl(bar.close)
                ctx._equity_curve.append(ctx.equity + unreal)

        return ctx._build_stats()
