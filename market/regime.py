"""
Market regime detection and classification.
"""
from enum import Enum
from typing import Dict


class MarketRegime(Enum):
    """Market regime enumeration."""
    STRONG_UPTREND = "strong_uptrend"
    STRONG_DOWNTREND = "strong_downtrend"
    WEAK_UPTREND = "weak_uptrend"
    WEAK_DOWNTREND = "weak_downtrend"
    BREAKOUT_CONSOLIDATION = "breakout_consolidation"
    HIGH_VOL_CONSOLIDATION = "high_vol_consolidation"
    LOW_VOL_CONSOLIDATION = "low_vol_consolidation"
    REVERSAL_PATTERN = "reversal_pattern"
    HIGH_VOLUME_ACCUMULATION = "high_volume_accumulation"
    HIGH_VOLUME_DISTRIBUTION = "high_volume_distribution"
    SIDEWAYS = "sideways"
    UNKNOWN = "unknown"


def enhanced_market_regime_detection_single(indicators_1h: Dict) -> MarketRegime:
    """
    Enhanced market regime detection based on 1h indicators.
    
    Args:
        indicators_1h: Dictionary of 1h timeframe indicators
        
    Returns:
        MarketRegime enum value
    """
    volatility = indicators_1h.get('volatility', 10)
    rsi = indicators_1h.get('rsi', 50)
    trend_strength = indicators_1h.get('trend_strength', 0.5)
    volume_ratio = indicators_1h.get('volume_ratio', 1.0)
    momentum = indicators_1h.get('momentum_1h', 0)
    
    # High volatility consolidation
    if volatility > 30 and abs(trend_strength) < 0.3:
        return MarketRegime.HIGH_VOL_CONSOLIDATION
    
    # Low volatility consolidation
    if volatility < 8 and abs(trend_strength) < 0.3:
        return MarketRegime.LOW_VOL_CONSOLIDATION
    
    # Strong uptrend
    if trend_strength > 0.75 and rsi > 55 and momentum > 0:
        return MarketRegime.STRONG_UPTREND
    
    # Weak uptrend
    if trend_strength > 0.45 and rsi > 48 and momentum > 0:
        return MarketRegime.WEAK_UPTREND
    
    # Strong downtrend
    if trend_strength < -0.75 and rsi < 45 and momentum < 0:
        return MarketRegime.STRONG_DOWNTREND
    
    # Weak downtrend
    if trend_strength < -0.45 and rsi < 52 and momentum < 0:
        return MarketRegime.WEAK_DOWNTREND
    
    # Sideways
    if abs(trend_strength) < 0.3 and 40 < rsi < 60:
        return MarketRegime.SIDEWAYS
    
    # Default to unknown
    return MarketRegime.UNKNOWN


__all__ = ['MarketRegime', 'enhanced_market_regime_detection_single']

