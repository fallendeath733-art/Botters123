"""
Global constants and configuration variables.
"""
import os
from collections import deque

# Parallel processing settings
ENABLE_PARALLEL_PROCESSING = True
MAX_CONCURRENT_SYMBOLS = 20
BATCH_SIZE_MARKET_SCAN = 10

# Thread configuration for numpy/OpenBLAS
try:
    import os as _os_config
    _os_config.environ['OMP_NUM_THREADS'] = '18'
    _os_config.environ['OPENBLAS_NUM_THREADS'] = '18'
    _os_config.environ['MKL_NUM_THREADS'] = '18'
    _os_config.environ['NUMEXPR_NUM_THREADS'] = '18'
except Exception:
    pass

# Trading settings
POLL_INTERVAL = 2.0
DEMO_MODE = True
SELECTED_EXCHANGE = os.getenv("SELECTED_EXCHANGE", "BYBIT").upper()
VALIDATION_MODE = "FLEXIBLE"
DISABLE_SSL_VERIFICATION = False
CONFIG_FILE = "config.txt"
CONFIG = {}

# CoinMarketCap settings
CMC_API_KEY = None
CMC_API_URL = "https://pro-api.coinmarketcap.com/v1"
COIN_MARKET_DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "coin_market_data.json")
CMC_CACHE_DEBUG = bool(int(os.getenv('CMC_CACHE_DEBUG', '0')))

# Candle persistence settings
PERSIST_CANDLES = bool(int(os.getenv('PERSIST_CANDLES', '1')))
CANDLE_CACHE_DIR = os.getenv('CANDLE_CACHE_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", '.cache', 'candles'))
CANDLE_AUTO_FLUSH_SEC = int(os.getenv('CANDLE_AUTO_FLUSH_SEC', '300'))
CANDLE_MAX_AGE = {
    '1m': int(os.getenv('CANDLE_MAX_AGE_1M', '300')),
    '3m': int(os.getenv('CANDLE_MAX_AGE_3M', '600')),
    '5m': int(os.getenv('CANDLE_MAX_AGE_5M', '600')),
    '15m': int(os.getenv('CANDLE_MAX_AGE_15M', '900')),
    '30m': int(os.getenv('CANDLE_MAX_AGE_30M', '1800')),
    '1h': int(os.getenv('CANDLE_MAX_AGE_1H', '3600')),
    '2h': int(os.getenv('CANDLE_MAX_AGE_2H', '7200')),
    '4h': int(os.getenv('CANDLE_MAX_AGE_4H', '14400')),
    '6h': int(os.getenv('CANDLE_MAX_AGE_6H', '21600')),
    '12h': int(os.getenv('CANDLE_MAX_AGE_12H', '43200')),
    '1d': int(os.getenv('CANDLE_MAX_AGE_1D', '86400')),
    '1w': int(os.getenv('CANDLE_MAX_AGE_1W', '604800')),
    '1M': int(os.getenv('CANDLE_MAX_AGE_1M', '2592000'))
}

# CMC rate limiting
_cmc_request_timestamps = deque(maxlen=300)
_cmc_rate_lock = None

# History tracking for coin selection (STAGE 1 & 2)
_coin_selection_orderflow_history = {}  # Track buy pressure over time
_coin_selection_orderbook_history = {}  # Track BIR over time
_coin_selection_spread_history = {}  # Track spread over time
_coin_selection_depth_history = {}  # Track market depth over time

__all__ = [
    'ENABLE_PARALLEL_PROCESSING', 'MAX_CONCURRENT_SYMBOLS', 'BATCH_SIZE_MARKET_SCAN',
    'POLL_INTERVAL', 'DEMO_MODE', 'SELECTED_EXCHANGE', 'VALIDATION_MODE',
    'DISABLE_SSL_VERIFICATION', 'CONFIG_FILE', 'CONFIG',
    'CMC_API_KEY', 'CMC_API_URL', 'COIN_MARKET_DATA_FILE', 'CMC_CACHE_DEBUG',
    'PERSIST_CANDLES', 'CANDLE_CACHE_DIR', 'CANDLE_AUTO_FLUSH_SEC', 'CANDLE_MAX_AGE',
    '_cmc_request_timestamps', '_cmc_rate_lock',
    '_coin_selection_orderflow_history', '_coin_selection_orderbook_history',
    '_coin_selection_spread_history', '_coin_selection_depth_history',
]

