"""
Enhanced risk management for position sizing and exposure control.
"""
from typing import Optional
from config.trading_config import TradingConfig


class EnhancedRiskManager:
    """Enhanced risk management with position sizing and sector exposure."""
    
    def __init__(self):
        self.position_sizes = {}
        self.correlation_matrix = {}
        self.sector_exposure = {}
        self.volatility_thresholds = {
            'low': 8.0,
            'medium': 18.0,
            'high': 30.0
        }

    def calculate_position_size(self, symbol: str, volatility: float, portfolio_value: float, market_regime=None) -> float:
        """Calculate position size based on volatility and market regime."""
        # Import here to avoid circular dependency
        from market.regime import MarketRegime
        
        base_size = TradingConfig.MAX_POSITION_SIZE
        if volatility > 45:
            size_multiplier = 0.1
        elif volatility > 40:
            size_multiplier = 0.2
        elif volatility > 35:
            size_multiplier = 0.3
        elif volatility > self.volatility_thresholds['high']:
            size_multiplier = 0.5
        elif volatility > self.volatility_thresholds['medium']:
            size_multiplier = 0.75
        elif volatility > 8:
            size_multiplier = 0.9
        else:
            size_multiplier = 1.0
        
        if market_regime:
            if market_regime == MarketRegime.SIDEWAYS:
                size_multiplier *= 0.4
            elif market_regime == MarketRegime.STRONG_UPTREND:
                size_multiplier *= 1.3
            elif market_regime == MarketRegime.WEAK_UPTREND:
                size_multiplier *= 1.1
            elif market_regime == MarketRegime.STRONG_DOWNTREND:
                size_multiplier *= 0.5
            elif market_regime == MarketRegime.WEAK_DOWNTREND:
                size_multiplier *= 0.7
            elif market_regime == MarketRegime.HIGH_VOL_CONSOLIDATION:
                size_multiplier *= 0.25
        
        position_size = base_size * size_multiplier * portfolio_value
        return max(position_size, portfolio_value * 0.005)

    def check_sector_exposure(self, symbol: str, new_position_size: float) -> bool:
        """Check if sector exposure limit is not exceeded."""
        sector = self.get_coin_sector(symbol)
        current_sector_exposure = self.sector_exposure.get(sector, 0)
        max_sector_exposure = 0.3
        return (current_sector_exposure + new_position_size) <= max_sector_exposure

    def get_coin_sector(self, symbol: str) -> str:
        """Get coin sector classification."""
        return 'GENERAL'


# Global risk manager instance
risk_manager = EnhancedRiskManager()

__all__ = ['EnhancedRiskManager', 'risk_manager']

