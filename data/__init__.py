"""
Data management modules for trading bot.
Contains candle data, price data, cache management, and CoinMarketCap integration.
"""

from .cache import (
    load_coin_market_cache,
    save_coin_market_cache,
    load_persisted_candles,
    save_persisted_candles,
    periodic_candle_cache_flush,
    get_coin_market_data,
    _get_cmc_rate_lock,
    cmc_rate_limit_guard,
)
from .validation import (
    DataQualityValidator,
    enhanced_validate_candles,
    validate_candles,
)

__all__ = [
    # Cache
    'load_coin_market_cache', 'save_coin_market_cache',
    'load_persisted_candles', 'save_persisted_candles',
    'periodic_candle_cache_flush', 'get_coin_market_data',
    '_get_cmc_rate_lock', 'cmc_rate_limit_guard',
    # Validation
    'DataQualityValidator', 'enhanced_validate_candles', 'validate_candles',
]

