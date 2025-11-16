"""
Funding Rate Analysis Module
Analisa funding rate untuk futures sentiment
"""

from typing import Dict, Optional, Tuple


def analyze_funding_rate_signal(funding_rate: float) -> Dict:
    """
    Analyze funding rate untuk sentiment signal
    
    Args:
        funding_rate: Funding rate (typically -0.01 to 0.01, or -1% to 1%)
    
    Returns:
        Dict dengan sentiment analysis
    """
    # Normalize funding rate to percentage if needed
    if abs(funding_rate) > 1.0:
        # Assume it's already in percentage (e.g., 0.01 = 1%)
        funding_rate_pct = funding_rate
    else:
        # Assume it's in decimal (e.g., 0.01 = 1%)
        funding_rate_pct = funding_rate * 100
    
    # Analyze sentiment
    if funding_rate_pct > 0.1:  # > 0.1% (longs paying shorts)
        sentiment = 'BEARISH'  # Too many longs, potential correction
        signal = 'SELL'
        strength = min(1.0, funding_rate_pct / 0.5)  # Normalize to 0-1
    elif funding_rate_pct < -0.1:  # < -0.1% (shorts paying longs)
        sentiment = 'BULLISH'  # Too many shorts, potential squeeze
        signal = 'BUY'
        strength = min(1.0, abs(funding_rate_pct) / 0.5)
    else:  # -0.1% to 0.1%
        sentiment = 'NEUTRAL'
        signal = 'NEUTRAL'
        strength = 0.0
    
    return {
        'funding_rate': funding_rate,
        'funding_rate_pct': funding_rate_pct,
        'sentiment': sentiment,
        'signal': signal,
        'strength': strength,
        'interpretation': f"Funding rate {funding_rate_pct:+.3f}% indicates {sentiment.lower()} sentiment"
    }


__all__ = [
    'analyze_funding_rate_signal'
]

