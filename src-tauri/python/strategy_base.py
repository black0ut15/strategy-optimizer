"""Strategy base class and parameter definition.

All strategies inherit from Strategy and declare their parameters
via a class-level `params` dict of Param objects.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class Param:
    """Declares a sweepable strategy parameter.

    Args:
        default: Default value.
        min: Minimum value for sweep range.
        max: Maximum value for sweep range.
        step: Step size for sweep.
        group: Display group name (from Pine Script).
        tooltip: Description text.
        options: List of allowed values (for string/enum params).
    """
    default: Any
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    group: str = ""
    tooltip: str = ""
    options: Optional[list] = None


class ParamAccessor:
    """Provides self.p.param_name access to current parameter values."""

    def __init__(self, values: Dict[str, Any]):
        self._values = values

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            return super().__getattribute__(name)
        if name in self._values:
            return self._values[name]
        raise AttributeError(f"No parameter named '{name}'")


class Strategy:
    """Base class for all trading strategies.

    Subclasses must:
    1. Define a class-level `params` dict mapping names to Param objects.
    2. Implement `init(self, ctx)` to pre-compute indicators.
    3. Implement `on_bar(self, bar, ctx)` with per-bar trading logic.

    The backtester engine sets `self.p` to a ParamAccessor before calling
    init(), so strategies access parameters as `self.p.fast_limit`, etc.

    Example:
        class MyStrategy(Strategy):
            params = {
                "fast": Param(0.38, min=0.1, max=0.9, step=0.01),
                "slow": Param(0.035, min=0.01, max=0.1, step=0.005),
            }

            def init(self, ctx):
                self.indicator = ta.ema(ctx.bars.close, 20)

            def on_bar(self, bar, ctx):
                if ctx.position_size == 0 and self.indicator[ctx.bar_index] > bar.close:
                    ctx.entry("Long", "long", qty=1.0)
    """

    params: Dict[str, Param] = {}

    def init(self, ctx: "Context") -> None:
        """Called once before the first bar. Pre-compute indicators here."""
        pass

    def on_bar(self, bar: "BarData", ctx: "Context") -> None:
        """Called for each bar. Implement entry/exit logic here."""
        pass

    @classmethod
    def get_param_defaults(cls) -> Dict[str, Any]:
        """Returns dict of parameter names to their default values."""
        return {name: p.default for name, p in cls.params.items()}

    @classmethod
    def get_param_info(cls) -> list:
        """Returns parameter metadata for UI display."""
        result = []
        for name, p in cls.params.items():
            result.append({
                "name": name,
                "default": p.default,
                "min": p.min,
                "max": p.max,
                "step": p.step,
                "group": p.group,
                "tooltip": p.tooltip,
                "options": p.options,
                "type": type(p.default).__name__,
            })
        return result
