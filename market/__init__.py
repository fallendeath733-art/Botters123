"""
Market analysis modules for trading bot.
Contains market regime detection, recommendations, and technical indicators.
"""

from .regime import MarketRegime, enhanced_market_regime_detection_single
from .recommendation import ActionRecommendation, StrategyType, ActionAnalysis, AIStrategy

# Other market modules will be added later
# from .indicators import calculate_advanced_indicators, calculate_simple_indicators, calculate_ai_score

__all__ = [
    'MarketRegime', 'enhanced_market_regime_detection_single',
    'ActionRecommendation', 'StrategyType', 'ActionAnalysis', 'AIStrategy',
]

