"""Signal strategies. Each is independent and emits its own Signal objects."""
from .liquidity_sweep import LiquiditySweepMSS
from .ob_fvg import OrderBlockFVG

STRATEGIES = [LiquiditySweepMSS(), OrderBlockFVG()]

__all__ = ["STRATEGIES", "LiquiditySweepMSS", "OrderBlockFVG"]
