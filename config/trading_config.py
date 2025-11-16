"""
Trading configuration constants and settings.
"""

class TradingConfig:
    """Centralized trading configuration class."""
    
    MAX_POSITION_SIZE = 0.1
    MAX_DAILY_LOSS = 0.02
    MAX_DRAWDOWN = 0.05
    MAX_TRADES_PER_DAY = 10
    MAX_PORTFOLIO_EXPOSURE = 0.75
    MAX_SIMULTANEOUS_POSITIONS = 5
    MAX_CORRELATED_EXPOSURE = 0.40
    DRAWDOWN_ALERT_THRESHOLD = 0.03
    MAX_REQUESTS_PER_MINUTE = 100
    RATE_LIMIT_BUFFER = 0.9
    TRADING_HOURS_START = 0
    TRADING_HOURS_END = 24
    MAX_VOLATILITY = 75.0
    MIN_VOLATILITY = 5.0
    ENABLE_TRADE_LOGGING = True
    ENABLE_ERROR_LOGGING = True
    ENABLE_SLIPPAGE_TRACKING = True
    TRADE_LOG_FILE = "trades_audit.csv"
    ERROR_LOG_FILE = "errors_log.csv"
    SLIPPAGE_LOG_FILE = "slippage_report.csv"
    MAX_ACCEPTABLE_SLIPPAGE = 0.5
    ENABLE_BUY_GUARD_SPREAD = True
    ENABLE_BUY_GUARD_DEBOUNCE = True
    ENABLE_BUY_GUARD_ASK_WALL = True
    ENABLE_BUY_GUARD_WHALE_WALL = True  # International standard: Whale wall detection
    ENABLE_BUY_GUARD_DEPTH = True  # International standard: Market depth validation
    ENABLE_BUY_GUARD_VOLUME = True  # International standard: Volume validation
    ENABLE_BUY_GUARD_SLIPPAGE = True  # International standard: Slippage estimation
    ENABLE_GUARD_DEBUG_LOGS = False  # Set to False to reduce spam, enable only for debugging
    DEBOUNCE_SAMPLES = 2
    DEBOUNCE_INTERVAL_MS = 250
    ENABLE_WS_CACHE_STATS = True
    SHOW_DEPTH_USD_LOGS = True
    ENABLE_ANALYSIS_QUALITY_OUTPUT = True
    TIMEOUT_MINUTES = {
        'STRONG_UPTREND': {
            'BLUECHIP': 20,
            'MAJOR': 15,
            'ALTCOIN': 12,
            'MICROCAP': 10,
        },
        'WEAK_UPTREND': {
            'BLUECHIP': 35,
            'MAJOR': 28,
            'ALTCOIN': 22,
            'MICROCAP': 18,
        },
    }
    DEFAULT_TIMEOUT_POLICY = None
    VOL_ENFORCE_TIMEOUT = {
        'enabled': False,
        'threshold': 40.0,
        'minutes': 12
    }

__all__ = ['TradingConfig']

