"""Algorithmic trading strategies for automated market entry/exit."""

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class Strategy(ABC):
    """Base class for all algo trading strategies."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def analyze(self, market_data: dict) -> dict | None:
        """Analyze market and return trade signal or None.
        
        Returns dict with:
        - token_id: str
        - side: "BUY" | "SELL"
        - outcome: str
        - confidence: float (0-1)
        - reason: str
        - suggested_size: float (USDC)
        """
        pass


class MomentumStrategy(Strategy):
    """Buy rising markets, sell falling ones (trend following)."""

    def __init__(self):
        super().__init__("Momentum")

    async def analyze(self, market_data: dict) -> dict | None:
        """Look for strong price momentum."""
        # market_data should have: token_id, current_price, price_24h_ago, volume_24h, etc.
        token_id = market_data.get("token_id")
        current = float(market_data.get("current_price", 0))
        prev = float(market_data.get("price_24h_ago", current))
        volume = float(market_data.get("volume_24h", 0))
        
        if not token_id or current <= 0 or prev <= 0:
            return None
        
        # Calculate momentum
        change_pct = (current - prev) / prev
        
        # Strong upward momentum + high volume = BUY signal
        if change_pct > 0.10 and volume > 10000:  # 10%+ gain + $10k volume
            return {
                "token_id": token_id,
                "side": "BUY",
                "outcome": market_data.get("outcome", ""),
                "confidence": min(abs(change_pct), 0.95),
                "reason": f"Strong upward momentum: +{change_pct*100:.1f}% in 24h",
                "suggested_size": 15.0,
            }
        
        # Strong downward momentum = SELL signal (if we hold)
        if change_pct < -0.10 and volume > 10000:
            return {
                "token_id": token_id,
                "side": "SELL",
                "outcome": market_data.get("outcome", ""),
                "confidence": min(abs(change_pct), 0.95),
                "reason": f"Strong downward momentum: {change_pct*100:.1f}% in 24h",
                "suggested_size": 15.0,
            }
        
        return None


class MeanReversionStrategy(Strategy):
    """Buy oversold, sell overbought (contrarian)."""

    def __init__(self):
        super().__init__("Mean Reversion")

    async def analyze(self, market_data: dict) -> dict | None:
        """Look for extreme deviations from average."""
        token_id = market_data.get("token_id")
        current = float(market_data.get("current_price", 0))
        avg_7d = float(market_data.get("avg_price_7d", current))
        
        if not token_id or current <= 0 or avg_7d <= 0:
            return None
        
        deviation = (current - avg_7d) / avg_7d
        
        # Price dropped significantly below 7-day avg = BUY (oversold)
        if deviation < -0.15:  # 15% below average
            return {
                "token_id": token_id,
                "side": "BUY",
                "outcome": market_data.get("outcome", ""),
                "confidence": min(abs(deviation), 0.90),
                "reason": f"Oversold: {deviation*100:.1f}% below 7-day average",
                "suggested_size": 12.0,
            }
        
        # Price rose significantly above 7-day avg = SELL (overbought)
        if deviation > 0.15:
            return {
                "token_id": token_id,
                "side": "SELL",
                "outcome": market_data.get("outcome", ""),
                "confidence": min(abs(deviation), 0.90),
                "reason": f"Overbought: +{deviation*100:.1f}% above 7-day average",
                "suggested_size": 12.0,
            }
        
        return None


class ValueStrategy(Strategy):
    """Buy underpriced outcomes, sell overpriced ones."""

    def __init__(self):
        super().__init__("Value")

    async def analyze(self, market_data: dict) -> dict | None:
        """Look for mispriced probabilities."""
        token_id = market_data.get("token_id")
        current = float(market_data.get("current_price", 0))
        fair_value = float(market_data.get("estimated_fair_value", current))
        
        if not token_id or current <= 0 or fair_value <= 0:
            return None
        
        # Significant discount to fair value = BUY
        if current < fair_value * 0.80:  # 20%+ discount
            edge = (fair_value - current) / current
            return {
                "token_id": token_id,
                "side": "BUY",
                "outcome": market_data.get("outcome", ""),
                "confidence": min(edge, 0.85),
                "reason": f"Undervalued: {edge*100:.0f}% edge vs fair value",
                "suggested_size": 20.0,
            }
        
        # Significant premium to fair value = SELL
        if current > fair_value * 1.20:  # 20%+ premium
            edge = (current - fair_value) / fair_value
            return {
                "token_id": token_id,
                "side": "SELL",
                "outcome": market_data.get("outcome", ""),
                "confidence": min(edge, 0.85),
                "reason": f"Overvalued: {edge*100:.0f}% premium vs fair value",
                "suggested_size": 20.0,
            }
        
        return None


# Strategy registry
STRATEGIES = {
    "momentum": MomentumStrategy(),
    "mean_reversion": MeanReversionStrategy(),
    "value": ValueStrategy(),
}


def get_strategy(name: str) -> Strategy | None:
    """Get strategy by name."""
    return STRATEGIES.get(name.lower())


def list_strategies() -> list[str]:
    """List all available strategy names."""
    return list(STRATEGIES.keys())
