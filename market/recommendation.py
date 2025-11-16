"""
Trading recommendations and AI strategy.
"""
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Any
from market.regime import MarketRegime


class ActionRecommendation(Enum):
    """Trading action recommendation."""
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    WAIT = "WAIT"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class StrategyType(Enum):
    """Strategy type enumeration."""
    MEAN_REVERSION = "mean_reversion"
    MOMENTUM_BREAKOUT = "momentum_breakout"
    VOLATILITY_EXPANSION = "volatility_expansion"
    TREND_FOLLOWING = "trend_following"
    RANGE_BOUND = "range_bound"
    BREAKOUT_ANTICIPATION = "breakout_anticipation"


@dataclass
class ActionAnalysis:
    """Analysis result for trading action."""
    recommendation: ActionRecommendation
    confidence: float
    reasons: List[str]
    price_targets: Dict[str, float]
    risk_level: str
    time_horizon: str
    score: float
    market_regime: MarketRegime
    ml_confidence_boost: float = 0.0
    sentiment_score: float = 0.5
    pattern_recognition: Dict[str, Any] = None


@dataclass
class AIStrategy:
    """AI-based trading strategy."""
    name: str
    description: str
    activation_price: float
    delta_percent: float
    confidence: float
    risk_reward_ratio: float
    timeframe: str
    market_regime: MarketRegime
    strategy_type: StrategyType = StrategyType.TREND_FOLLOWING
    historical_win_rate: float = 0.0
    expected_return: float = 0.0
    sharpe_ratio: float = 0.0
    ml_pattern_confidence: float = 0.0


__all__ = [
    'ActionRecommendation', 'StrategyType', 'ActionAnalysis', 'AIStrategy'
]

