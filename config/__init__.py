"""
Configuration module for trading bot.
Contains constants, trading config, and color definitions.
"""

from .colors import *
from .constants import *
from .trading_config import TradingConfig

__all__ = [
    # Colors
    'RESET', 'GREEN', 'RED', 'YELLOW', 'BLUE', 'CYAN', 'MAGENTA', 'WHITE',
    # Constants
    'POLL_INTERVAL', 'DEMO_MODE', 'SELECTED_EXCHANGE', 'VALIDATION_MODE',
    'DISABLE_SSL_VERIFICATION', 'CONFIG_FILE', 'CONFIG', 'CMC_API_KEY',
    'CMC_API_URL', 'COIN_MARKET_DATA_FILE', 'CMC_CACHE_DEBUG',
    'PERSIST_CANDLES', 'CANDLE_CACHE_DIR', 'CANDLE_AUTO_FLUSH_SEC',
    'CANDLE_MAX_AGE', 'ENABLE_PARALLEL_PROCESSING', 'MAX_CONCURRENT_SYMBOLS',
    'BATCH_SIZE_MARKET_SCAN',
    # Trading Config
    'TradingConfig',
]

