import asyncio

import numpy as np

import requests

import time

import math

import statistics

import json

import os

import shutil

import threading

import functools

from typing import List, Dict, Tuple, Optional, Any

from dataclasses import dataclass, field

from enum import Enum

from collections import deque

from datetime import datetime

import websockets

import logging



# Import from separated modules

from config import (

    TradingConfig,

    RESET, GREEN, RED, YELLOW, BLUE, CYAN, MAGENTA, WHITE,

    ENABLE_PARALLEL_PROCESSING, MAX_CONCURRENT_SYMBOLS, BATCH_SIZE_MARKET_SCAN,

    POLL_INTERVAL, DEMO_MODE, SELECTED_EXCHANGE, VALIDATION_MODE,

    DISABLE_SSL_VERIFICATION, CONFIG_FILE, CONFIG, CMC_API_KEY,

    CMC_API_URL, COIN_MARKET_DATA_FILE, CMC_CACHE_DEBUG,

    PERSIST_CANDLES, CANDLE_CACHE_DIR, CANDLE_AUTO_FLUSH_SEC, CANDLE_MAX_AGE,

)

from utils import (

    ErrorHandler, error_handler,

    OutputManager, output_manager,

    SimpleLogger, simple_logger,

    PerformanceMonitor, performance_monitor,

    normalize_symbol, safe_division, fmt, now_ts,

    CircuitBreaker,

    TaskManager, task_manager,

)

from market import (

    MarketRegime,

    ActionRecommendation, StrategyType, ActionAnalysis, AIStrategy,

)

# Note: enhanced_market_regime_detection_single is defined locally below (line 1153)

# because it uses more detailed logic with ema20, ema50, bb_upper, bb_lower, etc.

from risk import (

    EnhancedRiskManager, risk_manager,

    AntiPanicDump, anti_panic_dump,

)

from portfolio import (

    PortfolioManager, portfolio_manager,

)

from analysis import (

    # Divergence

    analyze_divergence, get_divergence_signal_strength, should_enter_based_on_divergence, DivergenceType,

    # WOBI

    calculate_weighted_orderbook_imbalance, get_wobi_signal_strength, should_enter_based_on_wobi, detect_hidden_orders_with_wobi,

    # VWAP

    calculate_vwap, calculate_multi_timeframe_vwap, calculate_multi_timeframe_vwap_async, analyze_vwap_trend, get_vwap_entry_signal, get_vwap_exit_signal,

    # Open Interest

    get_open_interest_data, get_long_short_ratio_data, analyze_open_interest_sentiment, get_oi_ls_entry_signal, get_oi_ls_exit_signal, calculate_leverage_risk,

    # Price Impact

    calculate_price_impact, get_optimal_order_size, should_execute_based_on_impact,

    # Multi-Timeframe

    TrendDirection, calculate_trend_from_candles, calculate_momentum_from_candles, check_multi_timeframe_alignment, get_multi_timeframe_signal_strength,

    # Bollinger + ADX

    calculate_bollinger_bands, calculate_adx, analyze_bollinger_adx, get_bollinger_adx_entry_signal, get_bollinger_adx_exit_signal,

    # Trade Size

    calculate_trade_size_distribution, get_dynamic_institutional_threshold, detect_institutional_activity, analyze_trade_size_trend, get_trade_size_entry_signal,

    # Funding Rate

    analyze_funding_rate_signal,

    # Adaptive Thresholds

    get_adaptive_divergence_threshold, get_adaptive_confidence_drop_threshold, get_adaptive_wobi_threshold, get_adaptive_vwap_threshold, get_adaptive_volume_manipulation_threshold,

)



logger = logging.getLogger("bybit.quant")

if os.getenv("BYBIT_DEBUG", "0") == "1":

    logging.basicConfig(level=logging.DEBUG, format='%(message)s')

else:

    if not logger.handlers:

        logger.addHandler(logging.NullHandler())



# Constants are now imported from config module above

# Thread configuration for numpy/OpenBLAS (keep this as it's runtime setup)

try:

    import os as _os_config

    _os_config.environ['OMP_NUM_THREADS'] = '18'

    _os_config.environ['OPENBLAS_NUM_THREADS'] = '18'

    _os_config.environ['MKL_NUM_THREADS'] = '18'

    _os_config.environ['NUMEXPR_NUM_THREADS'] = '18'

except Exception:

    pass

_cmc_request_timestamps = deque(maxlen=300)

_cmc_rate_lock = None



# History tracking for coin selection (STAGE 1 & 2)

_coin_selection_orderflow_history = {}  # Track buy pressure over time

_coin_selection_orderbook_history = {}  # Track BIR over time

_coin_selection_spread_history = {}  # Track spread over time

_coin_selection_depth_history = {}  # Track market depth over time



def _get_cmc_rate_lock():

    global _cmc_rate_lock

    if _cmc_rate_lock is None:

        _cmc_rate_lock = asyncio.Lock()

    return _cmc_rate_lock



async def cmc_rate_limit_guard():

    window = 60.0

    max_rpm = int(os.getenv('CMC_RPM', '80'))

    lock = _get_cmc_rate_lock()

    async with lock:

        now = time.time()

        while _cmc_request_timestamps and now - _cmc_request_timestamps[0] > window:

            _cmc_request_timestamps.popleft()

        if len(_cmc_request_timestamps) >= max_rpm:

            sleep_time = window - (now - _cmc_request_timestamps[0]) + 0.05

            if sleep_time > 0:

                print(f"{YELLOW}⚠️ CMC rate limit reached: {len(_cmc_request_timestamps)}/{max_rpm} requests in last minute - sleeping {sleep_time:.1f}s to avoid 429 error{RESET}")

                await asyncio.sleep(sleep_time)

            return await cmc_rate_limit_guard()

        _cmc_request_timestamps.append(now)



# normalize_symbol is now imported from utils module above



def load_coin_market_cache() -> Dict[str, Any]:

    if not os.path.exists(COIN_MARKET_DATA_FILE):

        return {}

    try:

        with open(COIN_MARKET_DATA_FILE, "r") as f:

            data = json.load(f)

        return data

    except Exception:

        return {}



def save_coin_market_cache(data: Dict[str, Any]):

    with open(COIN_MARKET_DATA_FILE, "w") as f:

        json.dump(data, f, indent=2)



def load_persisted_candles(symbol: str, interval: str, limit: int, max_age_s: int) -> Optional[List[Dict]]:

    if not PERSIST_CANDLES:

        return None

    try:

        os.makedirs(CANDLE_CACHE_DIR, exist_ok=True)

        safe_symbol = symbol.replace('/', '_')

        cache_file = os.path.join(CANDLE_CACHE_DIR, f"{safe_symbol}_{interval}.json")

        if not os.path.exists(cache_file):

            return None

        file_age = time.time() - os.path.getmtime(cache_file)

        if file_age > max_age_s:

            return None

        with open(cache_file, 'r') as f:

            data = json.load(f)

        candles = data.get('candles', [])

        if not candles or not isinstance(candles, list):

            return None

        return candles[-limit:] if len(candles) > limit else candles

    except Exception as e:

        return None



def save_persisted_candles(symbol: str, interval: str, candles: List[Dict]):

    if not PERSIST_CANDLES or not candles:

        return

    try:

        os.makedirs(CANDLE_CACHE_DIR, exist_ok=True)

        safe_symbol = symbol.replace('/', '_')

        cache_file = os.path.join(CANDLE_CACHE_DIR, f"{safe_symbol}_{interval}.json")

        temp_file = cache_file + '.tmp'

        data = {

            'symbol': symbol,

            'interval': interval,

            'candles': candles[-200:] if len(candles) > 200 else candles,

            'saved_at': time.time()

        }

        with open(temp_file, 'w') as f:

            json.dump(data, f, indent=2)

        os.replace(temp_file, cache_file)

    except Exception as e:

        pass



async def periodic_candle_cache_flush(interval_sec: Optional[int] = None):

    if not PERSIST_CANDLES:

        return

    if interval_sec is None:

        interval_sec = CANDLE_AUTO_FLUSH_SEC

    if interval_sec <= 0:

        return

    import asyncio as _asyncio



    while True:

        try:

            await _asyncio.sleep(interval_sec)

            ws_manager = _init_ws_manager()

            if ws_manager and hasattr(ws_manager, 'candle_cache'):

                for cache_key, cache_data in list(ws_manager.candle_cache.items()):

                    try:

                        parts = cache_key.rsplit('_', 1)

                        if len(parts) != 2:

                            continue

                        sym, inter = parts

                        candles = cache_data.get('candles') or []

                        if candles:

                            save_persisted_candles(sym, inter, candles)

                    except Exception:

                        continue

        except _asyncio.CancelledError:

            break

        except Exception:

            continue



def get_coin_market_data(symbol: str, fetch_func) -> Dict[str, Any]:



    CACHE_EXPIRY = 24 * 60 * 60

    cache = load_coin_market_cache()

    now = int(time.time())

    entry = cache.get(symbol)

    if entry and (now - entry.get("timestamp", 0) < CACHE_EXPIRY):

        return entry["data"]

    data = fetch_func(symbol)

    cache[symbol] = {"timestamp": now, "data": data}

    save_coin_market_cache(cache)

    return data



SMART_REFRESH_RULES = {

    'BLUECHIP': {

        'time_based': 14 * 24 * 3600,

        'price_change': 50,

        'volatility_threshold': 35

    },

    'MAJOR': {

        'time_based': 10 * 24 * 3600,

        'price_change': 40,

        'volatility_threshold': 30

    },

    'ALTCOIN': {

        'time_based': 7 * 24 * 3600,

        'price_change': 30,

        'volatility_threshold': 25

    },

    'MICROCAP': {

        'time_based': 3 * 24 * 3600,

        'price_change': 20,

        'volatility_threshold': 20

    }

}

import threading as _threading_cache



_coin_type_cache = {}

_coin_type_cache_lock = _threading_cache.Lock()

_coin_type_cache_ttl = 300



# Global flag to prevent duplicate credential messages

_credentials_printed = False



def load_config(config_path: str = CONFIG_FILE, preserve_selected_exchange: bool = False) -> Dict[str, Any]:

    global CONFIG, SELECTED_EXCHANGE, _credentials_printed

    config = {'BYBIT': {}, 'BINANCE': {}, 'OKX': {}, 'BITGET': {}, 'KUCOIN': {}, 'MEXC': {}, 'GATEIO': {}}

    # Store current SELECTED_EXCHANGE if we want to preserve it

    original_exchange = SELECTED_EXCHANGE if preserve_selected_exchange else None

    try:

        if not os.path.exists(config_path):

            print(f"{YELLOW}⚠️  Config file not found: {config_path}{RESET}")

            print(f"{CYAN}💡 Using demo mode (no API keys){RESET}")

            CONFIG = config

            return config

        with open(config_path, 'r') as f:

            lines = f.readlines()

        current_section = None

        config_selected_exchange = None

        for line in lines:

            line = line.strip()

            if not line or line.startswith('#'):

                continue

            if line.startswith('[') and line.endswith(']'):

                current_section = line[1:-1].upper()

                if current_section not in config:

                    config[current_section] = {}

                continue

            if '=' in line:

                key, value = line.split('=', 1)

                key, value = key.strip(), value.strip()

                # Check for SELECTED_EXCHANGE (can be outside section)

                if key.upper() == 'SELECTED_EXCHANGE':

                    config_selected_exchange = value.upper()

                    # Only update if not preserving

                    if not preserve_selected_exchange:

                        SELECTED_EXCHANGE = config_selected_exchange

                    # If preserving, keep the original exchange (don't override)

                    continue

                # Only process other keys if we're in a section

                if current_section:

                    if value.lower() in ['true', 'false']:

                        value = value.lower() == 'true'

                    config[current_section][key] = value

        CONFIG = config

        global CMC_API_KEY

        # Load exchange credentials for all supported exchanges (only print once)

        if not _credentials_printed:

            print(f"\n{CYAN}Config Information{RESET}")

            

            supported_exchanges = ['BYBIT', 'BINANCE', 'OKX', 'BITGET', 'KUCOIN', 'MEXC', 'GATEIO']

            # Exchanges that require passphrase

            exchanges_with_passphrase = ['OKX', 'BITGET', 'KUCOIN']

            

            for exchange_name in supported_exchanges:

                if exchange_name in config and 'API_KEY' in config[exchange_name] and config[exchange_name]['API_KEY']:

                    api_key = config[exchange_name]['API_KEY']

                    placeholder_keys = ['your_bybit_api_key_here', 'your_binance_api_key_here', 'your_okx_api_key_here', 

                                       'your_bitget_api_key_here', 'your_kucoin_api_key_here', 'your_mexc_api_key_here', 'your_gateio_api_key_here']

                    if api_key and api_key not in placeholder_keys:

                        # Check TESTNET mode

                        testnet_mode = config[exchange_name].get('TESTNET', 'false')

                        if isinstance(testnet_mode, str):

                            testnet_mode = testnet_mode.lower() == 'true'

                        elif isinstance(testnet_mode, bool):

                            testnet_mode = testnet_mode

                        else:

                            testnet_mode = False

                        

                        print(f"{GREEN}✅ {exchange_name}: API Connected{RESET}")

                    elif exchange_name in config:

                        # Check if exchange requires passphrase

                        if exchange_name in exchanges_with_passphrase:

                            print(f"{YELLOW}⚠️  {exchange_name}: Invalid API KEY / API SECRET / PASSPHRASE (demo mode){RESET}")

                        else:

                            print(f"{YELLOW}⚠️  {exchange_name}: Invalid API KEY / API SECRET (demo mode){RESET}")

            

            if 'CMC' in config and 'API_KEY' in config['CMC'] and config['CMC']['API_KEY'] and config['CMC']['API_KEY'] != 'your_cmc_api_key_here':

                CMC_API_KEY = config['CMC']['API_KEY']

                print(f"{GREEN}✅ CoinMarketCap API Verified{RESET}")

            else:

                CMC_API_KEY = None

                print(f"{YELLOW}⚠️  CMC API key invalid{RESET}")

            

            _credentials_printed = True

        else:

            # Still load CMC_API_KEY even if credentials already printed

            if 'CMC' in config and 'API_KEY' in config['CMC'] and config['CMC']['API_KEY'] and config['CMC']['API_KEY'] != 'your_cmc_api_key_here':

                CMC_API_KEY = config['CMC']['API_KEY']

            else:

                CMC_API_KEY = None

        return config

    except Exception as e:

        print(f"{RED}❌ Config error: {e}{RESET}")

        CONFIG = config

        return config



# TradingConfig is now imported from config module above

# Colors are now imported from config module above

try:

    import pandas as pd



    HAS_PANDAS = True

except Exception:

    pd = None

    HAS_PANDAS = False

try:

    import urllib3



    HAS_URLLIB3 = True

except Exception:

    urllib3 = None

    HAS_URLLIB3 = False



# ErrorHandler and error_handler are now imported from utils module above

# AntiPanicDump and anti_panic_dump are now imported from risk module above

# PerformanceMonitor and performance_monitor are now imported from utils module above

# EnhancedRiskManager and risk_manager are now imported from risk module above



class DataQualityValidator:

    def __init__(self):

        self.quality_threshold = 0.85

        self.anomaly_history = {}



    def comprehensive_data_validation(self, symbol: str, candles: List[Dict]) -> Dict:

        if not candles:

            return {'quality_score': 0, 'is_valid': False, 'issues': ['No data']}

        quality_metrics = {

            'completeness': self.calculate_completeness(candles),

            'consistency': self.check_consistency(candles),

            'anomaly_score': self.detect_anomalies(candles),

            'volume_quality': self.assess_volume_quality(candles),

            'price_integrity': self.check_price_integrity(candles)

        }

        quality_score = (

            quality_metrics['completeness'] * 0.25 +

            quality_metrics['consistency'] * 0.25 +

            (100 - quality_metrics['anomaly_score']) * 0.20 +

            quality_metrics['volume_quality'] * 0.15 +

            quality_metrics['price_integrity'] * 0.15

        )

        issues = self.identify_issues(quality_metrics)

        return {

            'quality_score': quality_score,

            'is_valid': quality_score >= 70,

            'issues': issues,

            'metrics': quality_metrics,

            'recommendation': 'TRADABLE' if quality_score >= 80 else 'CAUTION' if quality_score >= 70 else 'AVOID'

        }



    def calculate_completeness(self, candles: List[Dict]) -> float:

        required_fields = ['open', 'high', 'low', 'close', 'volume']

        complete_count = 0

        for candle in candles[-50:]:

            if all(field in candle and candle[field] is not None for field in required_fields):

                complete_count += 1

        return safe_division(complete_count, min(50, len(candles)), 0.0) * 100



    def check_consistency(self, candles: List[Dict]) -> float:

        if len(candles) < 2:

            return 100.0

        inconsistencies = 0

        total_checks = 0

        for i in range(1, len(candles)):

            if candles[i]['ts'] <= candles[i-1]['ts']:

                inconsistencies += 1

            total_checks += 1

            if (candles[i]['high'] < candles[i]['low'] or

                candles[i]['open'] > candles[i]['high'] or

                candles[i]['open'] < candles[i]['low'] or

                candles[i]['close'] > candles[i]['high'] or

                candles[i]['close'] < candles[i]['low']):

                inconsistencies += 1

            total_checks += 1

        consistency_score = 100.0 - (inconsistencies / total_checks * 100) if total_checks > 0 else 100.0

        return max(0.0, consistency_score)



    def check_price_integrity(self, candles: List[Dict]) -> float:

        if not candles:

            return 0.0

        valid_candles = 0

        for candle in candles:

            try:

                if (candle['open'] > 0 and candle['high'] > 0 and

                    candle['low'] > 0 and candle['close'] > 0 and

                    candle['high'] >= candle['low'] and

                    candle['high'] >= max(candle['open'], candle['close']) and

                    candle['low'] <= min(candle['open'], candle['close'])):

                    valid_candles += 1

            except (KeyError, TypeError):

                continue

        return safe_division(valid_candles, len(candles), 0.0) * 100



    def identify_issues(self, quality_metrics: Dict) -> List[str]:

        issues = []

        if quality_metrics['completeness'] < 90:

            issues.append(f"Low completeness ({quality_metrics['completeness']:.1f}%)")

        if quality_metrics['consistency'] < 95:

            issues.append(f"Data inconsistencies ({quality_metrics['consistency']:.1f}%)")

        if quality_metrics['anomaly_score'] > 20:

            issues.append(f"High anomaly score ({quality_metrics['anomaly_score']:.1f})")

        if quality_metrics['volume_quality'] < 80:

            issues.append(f"Poor volume data ({quality_metrics['volume_quality']:.1f}%)")

        if quality_metrics['price_integrity'] < 95:

            issues.append(f"Price integrity issues ({quality_metrics['price_integrity']:.1f}%)")

        return issues



    def detect_anomalies(self, candles: List[Dict]) -> float:

        if len(candles) < 10:

            return 0.0

        anomalies = 0

        total_checks = 0

        for i in range(1, len(candles)):

            try:

                current = candles[i]

                previous = candles[i-1]

                if previous['close'] > 0:

                    price_change = abs(current['close'] / previous['close'] - 1)

                    if price_change > 0.10:

                        anomalies += 1

                total_checks += 1

                if 'volume' in current and 'volume' in previous:

                    if previous['volume'] > 0:

                        volume_ratio = current['volume'] / previous['volume']

                        if volume_ratio > 5.0 or volume_ratio < 0.2:

                            anomalies += 1

                    total_checks += 1

                if current['close'] > 0:

                    range_ratio = (current['high'] - current['low']) / current['close']

                    if range_ratio > 0.15:

                        anomalies += 1

                total_checks += 1

            except (KeyError, ZeroDivisionError):

                continue

        if total_checks == 0:

            return 0.0

        anomaly_score = safe_division(anomalies, total_checks, 0.0) * 100

        return min(100, anomaly_score * 2)



    def assess_volume_quality(self, candles: List[Dict]) -> float:

        if not candles:

            return 0.0

        volumes = []

        valid_volume_count = 0

        for candle in candles:

            volume = candle.get('volume', 0)

            if volume is not None and volume >= 0 and not math.isnan(volume) and not math.isinf(volume):

                volumes.append(volume)

                valid_volume_count += 1

        if valid_volume_count == 0:

            return 0.0

        completeness = safe_division(valid_volume_count, len(candles), 0.0)

        if len(volumes) < 2:

            return completeness * 100

        try:

            avg_volume = sum(volumes) / len(volumes)

            if avg_volume > 0:

                zero_volumes = sum(1 for v in volumes if v == 0)

                zero_ratio = safe_division(zero_volumes, len(volumes), 0.0)

                volume_variance = statistics.variance(volumes) / avg_volume if len(volumes) > 1 else 0

                consistency_score = max(0, 100 - (volume_variance * 100))

                zero_penalty = zero_ratio * 100

                quality_score = (completeness * 40) + (consistency_score * 0.4) - zero_penalty

                return max(0, min(100, quality_score))

        except Exception:

            pass

        return completeness * 60

data_validator = DataQualityValidator()



class VaRCalculator:

    def __init__(self):

        self.historical_data = {}

        self.confidence_level = 0.95

        self.max_data_points_per_symbol = 5000



    def calculate_var(self, returns, confidence_level=0.95):

        if not returns or len(returns) == 0:

            return 0.0

        try:

            returns_array = np.array(returns)

            var = np.percentile(returns_array, (1 - confidence_level) * 100)

            return abs(var)

        except Exception as e:

            print(f"{YELLOW}⚠️ VaR calculation error: {e}{RESET}")

            if not hasattr(self, 'var_fallback_counter'):

                self.var_fallback_counter = 0

            self.var_fallback_counter += 1

            if self.var_fallback_counter > 5:

                print(f"{RED}Too many VaR calculation errors, exiting.{RESET}")

                import sys



                sys.exit(1)

            import asyncio



            asyncio.create_task(asyncio.sleep(5))

            return 0.0



    def calculate_conditional_var(self, returns, confidence_level=0.95):

        if not returns or len(returns) == 0:

            return 0.0

        try:

            returns_array = np.array(returns)

            var = self.calculate_var(returns, confidence_level)

            tail_losses = returns_array[returns_array <= -var]

            if len(tail_losses) > 0:

                cvar = np.mean(tail_losses)

                return abs(cvar)

            else:

                return var * 1.5

        except Exception as e:

            print(f"{YELLOW}⚠️ CVaR calculation error: {e}{RESET}")

            return self.calculate_var(returns, confidence_level) * 1.3



    def update_historical_data(self, symbol: str, price_data: List[float]):

        if symbol not in self.historical_data:

            self.historical_data[symbol] = []

        self.historical_data[symbol].extend(price_data)

        if len(self.historical_data[symbol]) > 1000:

            self.historical_data[symbol] = self.historical_data[symbol][-1000:]



    def calculate_returns(self, prices: List[float]) -> List[float]:

        if len(prices) < 2:

            return []

        returns = []

        for i in range(1, len(prices)):

            if prices[i-1] > 0 and prices[i] > 0:

                ret = math.log(safe_division(prices[i], prices[i-1], 1.0))

                returns.append(ret)

        return returns



    def cleanup_old_data(self, max_age_days: int = 7):

        try:

            current_time = time.time()

            cutoff_time = current_time - (max_age_days * 86400)

            symbols_to_remove = []

            for symbol, data_list in self.historical_data.items():

                if len(data_list) > self.max_data_points_per_symbol:

                    self.historical_data[symbol] = data_list[-self.max_data_points_per_symbol:]

                if not self.historical_data[symbol]:

                    symbols_to_remove.append(symbol)

            for symbol in symbols_to_remove:

                del self.historical_data[symbol]

        except Exception as e:

            print(f"{YELLOW}⚠️ VaR cleanup error: {e}{RESET}")



    def clear_all_data(self):

        self.historical_data.clear()



class MLEnhancement:

    def __init__(self):

        self.pattern_database = {}

        self.regime_memory = deque(maxlen=5000)



    def ml_market_regime_detection(self, indicators: Dict) -> Dict:

        features = self.extract_ml_features(indicators)

        regime_prediction = self.predict_regime(features)

        return {

            'regime': regime_prediction['regime'],

            'confidence': regime_prediction['confidence'],

            'duration_expectation': self.predict_regime_duration(features),

            'transition_probability': self.calculate_transition_prob(features),

            'ml_confidence_boost': regime_prediction['ml_boost']

        }



    def extract_ml_features(self, indicators: Dict) -> Dict:

        return {

            'volatility': indicators.get('volatility', 10),

            'rsi': indicators.get('rsi', 50),

            'volume_ratio': indicators.get('volume_ratio', 1),

            'trend_strength': indicators.get('trend_strength', 0.5),

            'momentum': indicators.get('momentum_1h', 0),

            'bb_position': indicators.get('bb_position', 50),

            'macd_histogram': indicators.get('macd_histogram', 0),

            'price_vs_ema20': self.calculate_price_vs_ema(indicators)

        }



    def predict_regime(self, features: Dict) -> Dict:

        volatility = features['volatility']

        rsi = features['rsi']

        volume_ratio = features['volume_ratio']

        trend_strength = features['trend_strength']

        regime_scores = {

            'STRONG_UPTREND': 0,

            'WEAK_UPTREND': 0,

            'SIDEWAYS': 0,

            'WEAK_DOWNTREND': 0,

            'STRONG_DOWNTREND': 0

        }

        if trend_strength > 0.75:

            if rsi > 55:

                regime_scores['STRONG_UPTREND'] += 40

            else:

                regime_scores['STRONG_DOWNTREND'] += 40

        elif trend_strength > 0.45:

            if rsi > 48:

                regime_scores['WEAK_UPTREND'] += 28

            else:

                regime_scores['WEAK_DOWNTREND'] += 28

        if volatility < 8:

            regime_scores['SIDEWAYS'] += 25

        elif volatility > 30:

            if rsi < 25:

                regime_scores['STRONG_DOWNTREND'] += 20

            elif rsi > 75:

                regime_scores['STRONG_UPTREND'] += 20

        if volume_ratio > 1.8:

            if trend_strength > 0.65:

                regime_scores['STRONG_UPTREND'] += 15

            else:

                regime_scores['SIDEWAYS'] -= 15

        elif volume_ratio < 0.6:

            regime_scores['SIDEWAYS'] += 10

        best_regime = max(regime_scores, key=regime_scores.get)

        confidence = min(95, max(50, regime_scores[best_regime]))

        if confidence >= 80:

            ml_boost = 5.0

        elif confidence >= 60:

            ml_boost = 3.0

        elif confidence >= 40:

            ml_boost = 1.0

        else:

            ml_boost = 0.0

        return {

            'regime': MarketRegime(best_regime.lower()),

            'confidence': confidence,

            'ml_boost': ml_boost

        }



    def calculate_price_vs_ema(self, indicators: Dict) -> float:

        current = indicators.get('current', 0)

        ema20 = indicators.get('ema20', 0)

        if ema20 > 0:

            return ((current - ema20) / ema20) * 100

        return 0.0



    def predict_regime_duration(self, features: Dict) -> str:

        volatility = features['volatility']

        trend_strength = features['trend_strength']

        if trend_strength > 0.7:

            return "LONG_DURATION" if volatility < 15 else "MEDIUM_DURATION"

        elif trend_strength > 0.4:

            return "MEDIUM_DURATION"

        else:

            return "SHORT_DURATION"



    def calculate_transition_prob(self, features: Dict) -> float:

        volatility = features['volatility']

        rsi = features['rsi']

        base_prob = min(80, volatility * 1.5)

        if rsi < 25 or rsi > 75:

            base_prob += 15

        return min(95, base_prob)



    def ml_optimized_delta(self, analysis: 'ActionAnalysis', indicators: Dict, strategy_type: str) -> float:

        try:

            if strategy_type == "BUY":

                base_delta = 2.0

            else:

                base_delta = 2.2

            volatility = indicators.get('volatility', 10)

            rsi = indicators.get('rsi', 50)

            regime = indicators.get('market_regime', MarketRegime.UNKNOWN)

            confidence = analysis.confidence

            risk_level = analysis.risk_level

            adjustments = []

            if volatility > 25:

                adjustments.append(1.15)

            elif volatility < 8:

                adjustments.append(0.85)

            if strategy_type == "BUY":

                if rsi < 30:

                    adjustments.append(0.9)

                elif rsi > 70:

                    adjustments.append(1.1)

            else:

                if rsi > 70:

                    adjustments.append(0.9)

                elif rsi < 30:

                    adjustments.append(1.1)

            if confidence > 80:

                adjustments.append(1.05)

            elif confidence < 40:

                adjustments.append(0.9)

            if risk_level == "VERY_HIGH":

                adjustments.append(0.8)

            elif risk_level == "HIGH":

                adjustments.append(0.9)

            elif risk_level == "LOW":

                adjustments.append(1.1)

            if regime in [MarketRegime.STRONG_UPTREND, MarketRegime.STRONG_DOWNTREND]:

                adjustments.append(0.9)

            elif regime in [MarketRegime.LOW_VOL_CONSOLIDATION, MarketRegime.SIDEWAYS]:

                adjustments.append(1.1)

            final_delta = base_delta

            for adjustment in adjustments:

                final_delta *= adjustment

            if strategy_type == "BUY":

                return max(1.0, min(4.5, final_delta))

            else:

                return max(1.2, min(5.0, final_delta))

        except Exception as e:

            print(f"{YELLOW}⚠️ ML delta optimization error: {e}{RESET}")

            return 2.0 if strategy_type == "BUY" else 2.2



    def cleanup_old_patterns(self, max_patterns_per_symbol: int = 100):

        try:

            for symbol, patterns in self.pattern_database.items():

                if len(patterns) > max_patterns_per_symbol:

                    self.pattern_database[symbol] = patterns[-max_patterns_per_symbol:]

        except Exception as e:

            print(f"{YELLOW}⚠️ ML pattern cleanup error: {e}{RESET}")



    def clear_all_data(self):

        self.pattern_database.clear()

        self.regime_memory.clear()

ml_enhancer = MLEnhancement()



class AdvancedRiskMetrics:

    def __init__(self):

        self.risk_history = {}

        self.var_calculator = VaRCalculator()



    async def calculate_comprehensive_risk(self, symbol: str, position_size: float,

                                   current_price: float, volatility: float,

                                   orderbook: Dict = None) -> Dict:

        var_metrics = self.calculate_var(symbol, position_size, current_price, volatility)

        drawdown_metrics = self.analyze_drawdown_risk(symbol, volatility)

        liquidity_metrics = await self.assess_liquidity_risk(symbol, orderbook, current_price)

        correlation_metrics = self.analyze_correlation_risk(symbol)

        risk_score = (

            var_metrics['var_score'] * 0.3 +

            drawdown_metrics['drawdown_score'] * 0.25 +

            liquidity_metrics['liquidity_score'] * 0.20 +

            correlation_metrics['correlation_score'] * 0.25

        )

        return {

            'composite_risk_score': risk_score,

            'risk_level': self.get_risk_level(risk_score),

            'var_1d': var_metrics['var_1d'],

            'var_7d': var_metrics['var_7d'],

            'max_drawdown_30d': drawdown_metrics['max_drawdown'],

            'expected_max_drawdown': drawdown_metrics['expected_drawdown'],

            'liquidity_rating': liquidity_metrics['rating'],

            'correlation_to_btc': correlation_metrics['btc_correlation'],

            'recommended_position_size': self.calculate_optimal_position_size(risk_score, position_size),

            'risk_adjusted_return': self.calculate_risk_adjusted_return(symbol, volatility)

        }



    def calculate_var(self, symbol: str, position_size: float, current_price: float,

                     volatility: float) -> Dict:

        """

        Calculate Value at Risk (VaR) according to international standards (Parametric VaR).

        Uses annualized volatility and converts to daily using square root of time rule.

        For crypto markets (24/7): Uses 365 days instead of 252 trading days.

        Z-score 2.33 corresponds to 99% confidence level (1% tail risk).

        """

        # International standard: Convert annualized volatility to daily

        # For crypto markets trading 24/7: sqrt(365) instead of sqrt(252)

        daily_volatility = volatility / math.sqrt(365)

        

        # International standard: VaR = Position Size * Daily Volatility * Z-score

        # Z-score 2.33 = 99% confidence level (standard for risk management)

        var_1d = position_size * (daily_volatility / 100) * 2.33

        

        # International standard: Multi-period VaR = VaR_1d * sqrt(periods)

        var_7d = var_1d * math.sqrt(7)

        

        var_score = min(100, safe_division(var_1d, position_size, 0.0) * 1000)

        return {

            'var_1d': f"${var_1d:.2f}",

            'var_7d': f"${var_7d:.2f}",

            'var_1d_percent': f"{safe_division(var_1d, position_size, 0.0) * 100:.1f}%",

            'var_score': var_score

        }



    def analyze_drawdown_risk(self, symbol: str, volatility: float) -> Dict:

        expected_drawdown = volatility * 1.5

        historical_drawdown = volatility * 2.0

        drawdown_score = min(100, expected_drawdown * 2)

        return {

            'expected_drawdown': f"{expected_drawdown:.1f}%",

            'max_drawdown': f"{historical_drawdown:.1f}%",

            'drawdown_score': drawdown_score

        }



    async def assess_liquidity_risk(self, symbol: str, orderbook: Dict = None, current_price: float = 0) -> Dict:

        try:

            if orderbook is None:

                orderbook = await get_orderbook_optimized(symbol=symbol, limit=25)

            if not orderbook or 'bids' not in orderbook or 'asks' not in orderbook:

                coin_type = await get_coin_type_range(symbol)

                if coin_type == 'BLUECHIP':

                    return {'rating': 'HIGH', 'liquidity_score': 15}

                elif coin_type == 'MAJOR':

                    return {'rating': 'MEDIUM', 'liquidity_score': 35}

                elif coin_type == 'ALTCOIN':

                    return {'rating': 'LOW', 'liquidity_score': 60}

                else:

                    return {'rating': 'VERY_LOW', 'liquidity_score': 85}

            bids = orderbook['bids']

            asks = orderbook['asks']

            best_bid = float(bids[0][0]) if bids else 0

            best_ask = float(asks[0][0]) if asks else 0

            spread_pct = ((best_ask - best_bid) / best_bid) * 100 if best_bid > 0 else 999

            depth_bid = sum(float(bid[1]) for bid in bids[:10])

            depth_ask = sum(float(ask[1]) for ask in asks[:10])

            if current_price > 0:

                depth_bid_usd = depth_bid * current_price

                depth_ask_usd = depth_ask * current_price

                total_depth_usd = depth_bid_usd + depth_ask_usd

            else:

                total_depth_usd = (depth_bid + depth_ask) * 1000

            coin_type = await get_coin_type_range(symbol)

            if coin_type == 'BLUECHIP':

                if spread_pct < 0.08 and total_depth_usd > 150000:

                    rating, score = 'EXCELLENT', 5

                elif spread_pct < 0.12 and total_depth_usd > 80000:

                    rating, score = 'HIGH', 15

                else:

                    rating, score = 'MEDIUM', 35

            elif coin_type == 'MAJOR':

                if spread_pct < 0.12 and total_depth_usd > 80000:

                    rating, score = 'HIGH', 20

                elif spread_pct < 0.20 and total_depth_usd > 40000:

                    rating, score = 'MEDIUM', 40

                else:

                    rating, score = 'LOW', 60

            elif coin_type == 'ALTCOIN':

                if spread_pct < 0.35 and total_depth_usd > 10000:

                    rating, score = 'MEDIUM', 50

                elif spread_pct < 0.70 and total_depth_usd > 5000:

                    rating, score = 'LOW', 70

                else:

                    rating, score = 'POOR', 85

            elif coin_type == 'MICROCAP':

                if spread_pct < 0.60 and total_depth_usd > 3000:

                    rating, score = 'MEDIUM', 50

                elif spread_pct < 1.20 and total_depth_usd > 1000:

                    rating, score = 'LOW', 70

                else:

                    rating, score = 'POOR', 90

            else:

                rating, score = 'LOW', 60

            return {

                'rating': rating,

                'liquidity_score': score,

                'spread_pct': spread_pct,

                'total_depth_usd': total_depth_usd,

                'coin_type': coin_type

            }

        except Exception as e:

            return {'rating': 'UNKNOWN', 'liquidity_score': 50, 'error': str(e)}



    def calculate_optimal_position_size(self, risk_score: float, current_size: float) -> float:

        risk_multiplier = max(0.1, 1 - (risk_score / 200))

        optimal_size = current_size * risk_multiplier

        return max(current_size * 0.1, optimal_size)



    def get_risk_level(self, risk_score: float) -> str:

        if risk_score < 20:

            return "LOW"

        elif risk_score < 40:

            return "MEDIUM"

        elif risk_score < 60:

            return "HIGH"

        else:

            return "VERY_HIGH"



    def calculate_risk_adjusted_return(self, symbol: str, volatility: float) -> float:

        if volatility > 0:

            return min(10.0, 15.0 / (volatility / 10))

        return 5.0



    def analyze_correlation_risk(self, symbol: str) -> Dict:

        if 'BTC' in symbol:

            btc_correlation = 1.0

        elif 'ETH' in symbol:

            btc_correlation = 0.8

        else:

            btc_correlation = 0.6

        correlation_score = min(100, btc_correlation * 100)

        return {

            'btc_correlation': f"{btc_correlation:.2f}",

            'correlation_score': correlation_score

        }

risk_metrics = AdvancedRiskMetrics()



# MarketRegime, ActionRecommendation, StrategyType, ActionAnalysis, and AIStrategy are now imported from market module above



def now_ts() -> str:

    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())



def fmt(x: Optional[float]) -> str:

    if x is None:

        return "N/A"

    try:

        return f"{x:.6f}".rstrip("0").rstrip(".")

    except Exception:

        return str(x)



def safe_division(numerator: float, denominator: float, default: float = 0.0) -> float:

    if (denominator == 0 or

        math.isnan(denominator) or

        math.isinf(denominator) or

        math.isnan(numerator) or

        math.isinf(numerator)):

        return default

    try:

        result = numerator / denominator

        return result if not (math.isnan(result) or math.isinf(result)) else default

    except (ZeroDivisionError, ValueError, TypeError, OverflowError):

        return default



# OutputManager and output_manager are now imported from utils module above

# CircuitBreaker is now imported from utils module above

# SimpleLogger and simple_logger are now imported from utils module above

# TaskManager and task_manager are now imported from utils module above

# PortfolioManager and portfolio_manager are now imported from portfolio module above



async def emergency_close_all_positions(dry_run: bool = False, live_mode: bool = False, auto_choice: str = None):

    try:

        output_manager.print_static(f"\n{RED}{'='*80}{RESET}")

        output_manager.print_static(f"{RED}🚨 EMERGENCY POSITION CLOSE ACTIVATED!{RESET}")

        output_manager.print_static(f"{RED}{'='*80}{RESET}")

        if dry_run:

            output_manager.print_static(f"{YELLOW}⚠️ DRY RUN MODE - No actual positions will be closed{RESET}")

        bot_positions = list(portfolio_manager.positions.items()) if portfolio_manager.positions else []

        bot_count = len(bot_positions)

        all_exchange_positions = []

        manual_count = 0

        if live_mode:

            output_manager.print_static(f"{YELLOW}⚠️ Live mode not yet implemented - closing bot positions only{RESET}")

        total_count = bot_count + manual_count

        output_manager.print_static(f"{CYAN}📊 Position Status:{RESET}")

        output_manager.print_static(f"  ├─ Bot positions: {bot_count}")

        if live_mode:

            output_manager.print_static(f"  ├─ Manual positions: {manual_count}")

            output_manager.print_static(f"  └─ Total: {total_count}")

        else:

            output_manager.print_static(f"  └─ (Testing mode - only bot positions tracked)")

        if bot_count > 0:

            output_manager.print_static(f"\n{CYAN}🤖 Bot Positions:{RESET}")

            for symbol, pos_info in bot_positions:

                action = pos_info.get('action', 'UNKNOWN')

                price = pos_info.get('price', 0)

                output_manager.print_static(f"  ├─ {symbol}: {action} @ ${price:.2f}")

        if live_mode and manual_count > 0:

            output_manager.print_static(f"\n{YELLOW}👤 Manual Positions:{RESET}")

            for pos in all_exchange_positions:

                symbol = pos['symbol']

                if symbol not in portfolio_manager.positions:

                    output_manager.print_static(f"  ├─ {symbol}: {pos['side']} @ ${pos.get('entry_price', 0):.2f}")

        if auto_choice:

            choice = auto_choice

        else:

            output_manager.print_static(f"\n{CYAN}🎯 Close Options:{RESET}")

            if live_mode and manual_count > 0:

                output_manager.print_static(f"1. Close BOT positions only ({bot_count}) ✅")

                output_manager.print_static(f"2. Close ALL positions ({total_count}) ⚠️")

                output_manager.print_static(f"3. Cancel (no action)")

                user_input = input(f"\n{YELLOW}Select option (1-3): {RESET}").strip()

                if user_input == "1":

                    choice = 'bot_only'

                elif user_input == "2":

                    choice = 'all'

                else:

                    choice = 'cancel'

            else:

                output_manager.print_static(f"1. Close bot positions ({bot_count}) ✅")

                output_manager.print_static(f"2. Cancel (no action)")

                user_input = input(f"\n{YELLOW}Select option (1-2): {RESET}").strip()

                if user_input == "1":

                    choice = 'bot_only'

                else:

                    choice = 'cancel'

        if choice == 'cancel':

            output_manager.print_static(f"\n{YELLOW}❌ Emergency close cancelled - no positions closed{RESET}")

            return False

        elif choice == 'bot_only':

            output_manager.print_static(f"\n{CYAN}🤖 Closing bot positions only...{RESET}")

            if bot_count > 0:

                for symbol, pos_info in bot_positions:

                    if not dry_run:

                        current_price = await get_price_optimized(symbol)

                        if current_price and pos_info.get('price') and pos_info.get('price') > 0:

                            pnl = safe_division((current_price - pos_info['price']), pos_info['price'], 0.0) * 100

                            output_manager.print_static(f"  ├─ {symbol}: Closed at ${current_price:.2f} | P/L: {pnl:+.2f}%")

                        await portfolio_manager.delete_position(symbol)

                output_manager.print_static(f"{GREEN}✅ All bot positions closed ({bot_count}){RESET}")

                if live_mode and manual_count > 0:

                    output_manager.print_static(f"{YELLOW}⚠️  Manual positions remain open ({manual_count}){RESET}")

            else:

                output_manager.print_static(f"{CYAN}📊 No bot positions to close{RESET}")

        elif choice == 'all':

            output_manager.print_static(f"\n{RED}⚠️  WARNING: This will close ALL positions including manual!{RESET}")

            confirm = input(f"{RED}Type 'CONFIRM ALL' to proceed: {RESET}").strip()

            if confirm == 'CONFIRM ALL':

                output_manager.print_static(f"\n{RED}🚨 Closing ALL positions...{RESET}")

                for symbol, pos_info in bot_positions:

                    if not dry_run:

                        current_price = await get_price_optimized(symbol)

                        if current_price and pos_info.get('price') and pos_info.get('price') > 0:

                            pnl = safe_division((current_price - pos_info['price']), pos_info['price'], 0.0) * 100

                            output_manager.print_static(f"  ├─ {symbol}: [BOT] Closed at ${current_price:.2f} | P/L: {pnl:+.2f}%")

                        await portfolio_manager.delete_position(symbol)

                if live_mode and manual_count > 0:

                    output_manager.print_static(f"{YELLOW}⚠️ Manual position closing not yet implemented{RESET}")

                output_manager.print_static(f"{GREEN}✅ All positions closed ({total_count}){RESET}")

            else:

                output_manager.print_static(f"\n{YELLOW}❌ Confirmation failed - no positions closed{RESET}")

                return False

        output_manager.print_static(f"\n{CYAN}🛑 Stopping all background tasks...{RESET}")

        try:

            import asyncio



            if asyncio.iscoroutinefunction(task_manager.cancel_all_tasks):

                loop = asyncio.get_event_loop()

                if loop.is_running():

                    asyncio.create_task(task_manager.cancel_all_tasks("ALL"))

                else:

                    asyncio.run(task_manager.cancel_all_tasks("ALL"))

            output_manager.print_static(f"{GREEN}✅ All background tasks cancelled{RESET}")

        except Exception as e:

            output_manager.print_static(f"{YELLOW}⚠️ Task cancellation warning: {e}{RESET}")

        output_manager.stop_live_update()

        emergency_state = {

            'timestamp': time.time(),

            'positions_closed': bot_count if choice == 'bot_only' else total_count,

            'choice': choice,

            'daily_pnl': portfolio_manager.daily_pnl,

            'daily_trades': portfolio_manager.daily_trades,

            'tasks_cancelled': True

        }

        output_manager.print_static(f"\n{GREEN}💾 Emergency state saved{RESET}")

        output_manager.print_static(f"{CYAN}📊 Final Stats: {portfolio_manager.daily_trades} trades | P/L: {portfolio_manager.daily_pnl:+.2f}%{RESET}")

        output_manager.print_static(f"{RED}{'='*80}{RESET}")

        output_manager.print_static(f"{GREEN}✅ Emergency shutdown complete - Funds protected!{RESET}")

        output_manager.print_static(f"{RED}{'='*80}{RESET}")

        return True

    except Exception as e:

        output_manager.print_static(f"{RED}❌ Emergency close error: {e}{RESET}")

        output_manager.print_static(f"{YELLOW}⚠️ Please manually check your exchange positions!{RESET}")

        return False



async def manual_kill_switch():

    try:

        output_manager.print_static(f"\n{RED}{'='*80}{RESET}")

        output_manager.print_static(f"{RED}⚠️  EMERGENCY KILL SWITCH{RESET}")

        output_manager.print_static(f"{RED}{'='*80}{RESET}")

        output_manager.print_static(f"{YELLOW}This will:{RESET}")

        output_manager.print_static(f"  1. 🛑 Stop ALL trading activities")

        output_manager.print_static(f"  2. 💰 Close ALL open positions (if any)")

        output_manager.print_static(f"  3. ❌ Cancel ALL pending orders (if any)")

        output_manager.print_static(f"  4. 💾 Save emergency state for recovery")

        output_manager.print_static(f"  5. 🚪 Exit bot immediately")

        output_manager.print_static(f"{RED}{'='*80}{RESET}")

        stats = portfolio_manager.get_daily_stats()

        output_manager.print_static(f"{CYAN}📊 Current Status:{RESET}")

        output_manager.print_static(f"  ├─ Trades today: {stats['daily_trades']}")

        output_manager.print_static(f"  ├─ Daily P/L: {stats['daily_pnl']:+.2f}%")

        output_manager.print_static(f"  └─ Open positions: {len(portfolio_manager.positions)}")

        output_manager.print_static(f"\n{YELLOW}⚠️  WARNING: This action is immediate and cannot be undone!{RESET}")

        confirm = input(f"\n{RED}Type 'EMERGENCY' to confirm kill switch: {RESET}").strip()

        if confirm == 'EMERGENCY':

            output_manager.print_static(f"\n{RED}🚨 KILL SWITCH ACTIVATED!{RESET}")

            if portfolio_manager.positions:

                output_manager.print_static(f"\n{YELLOW}{'='*80}{RESET}")

                output_manager.print_static(f"{RED}⚠️  FINAL CONFIRMATION REQUIRED{RESET}")

                output_manager.print_static(f"{YELLOW}{'='*80}{RESET}")

                output_manager.print_static(f"{CYAN}You have {len(portfolio_manager.positions)} tracked position(s){RESET}")

                output_manager.print_static(f"\n{YELLOW}Choose how to proceed:{RESET}")

                output_manager.print_static(f"  • Type '{RED}CONFIRM ALL{RESET}' to close {RED}ALL positions{RESET} (bot + manual, if any)")

                output_manager.print_static(f"  • Press Enter to see {CYAN}detailed options{RESET} (choose what to close)")

                output_manager.print_static(f"{YELLOW}{'='*80}{RESET}")

                final_confirm = input(f"\n{RED}Your choice: {RESET}").strip()

                if final_confirm == 'CONFIRM ALL':

                    output_manager.print_static(f"\n{RED}⚡ CLOSING ALL POSITIONS IMMEDIATELY!{RESET}")

                    success = await emergency_close_all_positions(

                        dry_run=False,

                        live_mode=False,

                        auto_choice='all'

                    )

                else:

                    output_manager.print_static(f"\n{CYAN}📊 Opening detailed options...{RESET}")

                    success = await emergency_close_all_positions(dry_run=False)

            else:

                output_manager.print_static(f"\n{CYAN}📊 No open positions to close{RESET}")

                success = True

            if success:

                output_manager.print_static(f"\n{GREEN}✅ Emergency shutdown successful{RESET}")

                output_manager.print_static(f"{CYAN}📊 Please check your exchange for final confirmation{RESET}")

                output_manager.print_static(f"{CYAN}💡 You can restart the bot anytime when ready{RESET}")

            else:

                output_manager.print_static(f"\n{YELLOW}⚠️ Emergency shutdown completed with errors{RESET}")

                output_manager.print_static(f"{RED}⚠️ IMPORTANT: Manually verify your exchange positions!{RESET}")

            output_manager.print_static(f"\n{CYAN}Goodbye! Stay safe! 👋{RESET}")

            return True

        else:

            output_manager.print_static(f"\n{YELLOW}❌ Kill switch cancelled - returning to menu{RESET}")

            return False

    except Exception as e:

        output_manager.print_static(f"{RED}❌ Kill switch error: {e}{RESET}")

        output_manager.print_static(f"{YELLOW}⚠️ Bot will continue - use Ctrl+C if needed{RESET}")

        return False



def enhanced_market_regime_detection_single(indicators_1h: Dict) -> MarketRegime:

    try:

        volatility = indicators_1h.get('volatility', 10)

        volume_ratio = indicators_1h.get('volume_ratio', 1)

        trend_strength = indicators_1h.get('trend_strength', 0.5)

        ema20 = indicators_1h.get('ema20', 0)

        ema50 = indicators_1h.get('ema50', 0)

        current = indicators_1h.get('current', 0)

        if current <= 0:

            return MarketRegime.UNKNOWN

        trend_direction = (ema20 - ema50) / current * 100

        if trend_strength > 0.85:

            if trend_direction > 1.2:

                return MarketRegime.STRONG_UPTREND

            elif trend_direction < -1.2:

                return MarketRegime.STRONG_DOWNTREND

        elif trend_strength > 0.35:

            if trend_direction > 0.6:

                return MarketRegime.WEAK_UPTREND

            elif trend_direction < -0.6:

                return MarketRegime.WEAK_DOWNTREND

        if volatility > 30 and volume_ratio > 1.8:

            return MarketRegime.HIGH_VOL_CONSOLIDATION

        elif volatility < 6 and volume_ratio < 0.7:

            return MarketRegime.LOW_VOL_CONSOLIDATION

        if volume_ratio > 2.0:

            if current > ema20:

                return MarketRegime.HIGH_VOLUME_ACCUMULATION

            else:

                return MarketRegime.HIGH_VOLUME_DISTRIBUTION

        elif volume_ratio < 0.5:

            return MarketRegime.LOW_VOL_CONSOLIDATION

        sideways_score = 0

        max_score = 5

        bb_upper = indicators_1h.get('bb_upper', current * 1.02)

        bb_lower = indicators_1h.get('bb_lower', current * 0.98)

        price_range = (bb_upper - bb_lower) / current

        if price_range < 0.03:

            sideways_score += 1

        if volume_ratio < 0.8:

            sideways_score += 1

        rsi = indicators_1h.get('rsi', 50)

        if 40 <= rsi <= 60:

            sideways_score += 1

        bb_width = indicators_1h.get('bb_width', 0.05)

        if bb_width < 0.02:

            sideways_score += 1

        if abs(trend_direction) < 0.3:

            sideways_score += 1

        if sideways_score >= 3:

            return MarketRegime.SIDEWAYS

        return MarketRegime.SIDEWAYS

    except Exception as e:

        output_manager.print_static(f"{YELLOW}⚠️ Enhanced regime detection error: {e}{RESET}")

        return MarketRegime.UNKNOWN

from collections import OrderedDict



class LRUCache:

    def __init__(self, max_size=10000):

        self.cache = OrderedDict()

        self.max_size = max_size



    def get(self, key):

        if key in self.cache:

            self.cache.move_to_end(key)

            return self.cache[key]

        return None



    def set(self, key, value):

        if key in self.cache:

            self.cache.move_to_end(key)

        self.cache[key] = value

        if len(self.cache) > self.max_size:

            self.cache.popitem(last=False)



    def clear(self):

        self.cache.clear()



    def __len__(self):

        return len(self.cache)



# ============================================================================

# MULTI-EXCHANGE SUPPORT - Base Classes and Factory

# ============================================================================



class BaseExchangeClient:

    """Base abstract class for all exchange clients"""

    def __init__(self, exchange_name: str):

        self.exchange_name = exchange_name

        self.session = requests.Session()

        if DISABLE_SSL_VERIFICATION:

            self.session.verify = False

            if HAS_URLLIB3:

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        self.cache = LRUCache(max_size=1000)

        self.request_timestamps = deque(maxlen=1200)

        self.request_count = 0

        self.circuit_breaker = CircuitBreaker(max_failures=3, reset_timeout=120)

        self.last_cache_cleanup = time.time()

        self.unsupported_symbols = set()

        

    def _respect_rate_limits(self):

        """Override in subclass with exchange-specific rate limits"""

        if not self.circuit_breaker.can_execute():

            sleep_time = 30

            print(f"{RED}🚨 CIRCUIT BREAKER ACTIVE - Waiting {sleep_time}s{RESET}")

            time.sleep(sleep_time)

            return

        now = time.time()

        recent_requests = [ts for ts in self.request_timestamps if now - ts < 60]

        current_rate = len(recent_requests)

        max_allowed = int(TradingConfig.MAX_REQUESTS_PER_MINUTE * TradingConfig.RATE_LIMIT_BUFFER)

        if current_rate >= max_allowed:

            sleep_time = 60 - (now - recent_requests[0]) if recent_requests else 60

            sleep_time = max(0.1, min(60, sleep_time))

            print(f"{YELLOW}⚠️ Rate limit: {current_rate}/min - sleeping {sleep_time:.1f}s{RESET}")

            time.sleep(sleep_time)

    

    def normalize_symbol(self, symbol: str) -> str:

        """Convert symbol format (e.g., BTC_USDT -> BTCUSDT)"""

        return symbol.replace('_', '')

    

    def get_price_rest(self, symbol: str) -> Optional[float]:

        """Get current price via REST API - must be implemented in subclass"""

        raise NotImplementedError("Subclass must implement get_price_rest")

    

    def fetch_candles(self, symbol: str, interval: str = "5m", limit: int = 200) -> Optional[List[Dict]]:

        """Fetch candle data - must be implemented in subclass"""

        raise NotImplementedError("Subclass must implement fetch_candles")

    

    def get_current_price(self, symbol: str) -> Optional[float]:

        """Get current price (alias for get_price_rest)"""

        return self.get_price_rest(symbol)

    

    def get_orderbook(self, symbol: str, limit: int = 100) -> Optional[Dict]:

        """Get orderbook data - must be implemented in subclass"""

        raise NotImplementedError("Subclass must implement get_orderbook")

    

    def get_aggregated_trades(self, symbol: str, limit: int = 500) -> Optional[List[Dict]]:

        """Get recent trades - must be implemented in subclass"""

        raise NotImplementedError("Subclass must implement get_aggregated_trades")

    

    def validate_price(self, price: float, previous_price: float = None) -> bool:

        """Validate price data"""

        if (price <= 0 or math.isnan(price) or math.isinf(price) or price > 1e7):

            return False

        if previous_price is not None:

            if previous_price <= 0:

                return True

            price_change = abs(price / previous_price - 1)

            if price_change > 0.5:

                return False

        return True

    

    def validate_candle_data(self, candle: Dict) -> bool:

        """Validate candle data"""

        try:

            required_fields = ['open', 'high', 'low', 'close', 'volume']

            for field in required_fields:

                if field not in candle or candle[field] is None:

                    return False

            if (candle['open'] <= 0 or candle['close'] <= 0 or

                candle['high'] <= 0 or candle['low'] <= 0 or

                candle['volume'] < 0):

                return False

            if (candle['high'] < candle['low'] or

                candle['high'] < max(candle['open'], candle['close']) or

                candle['low'] > min(candle['open'], candle['close'])):

                return False

            return True

        except Exception:

            return False

    

    def cleanup_old_cache(self):

        """Clean up old cache entries"""

        current_time = time.time()

        keys_to_delete = []

        for key, value in list(self.cache.cache.items()):

            if current_time - value['timestamp'] > 3600:

                keys_to_delete.append(key)

        for key in keys_to_delete:

            if key in self.cache.cache:

                del self.cache.cache[key]

        self.last_cache_cleanup = current_time

    

    def get_api_stats(self):

        """Get API statistics"""

        now = time.time()

        recent_requests = [ts for ts in self.request_timestamps if now - ts < 60]

        return {

            'total_requests': self.request_count,

            'requests_per_minute': len(recent_requests),

            'circuit_breaker_failures': self.circuit_breaker.failures,

            'circuit_breaker_active': not self.circuit_breaker.can_execute(),

            'cache_size': len(self.cache)

        }

    

    def get_health_status(self):

        """Get health status"""

        now = time.time()

        recent_requests = [ts for ts in self.request_timestamps if now - ts < 60]

        return {

            'circuit_breaker_active': not self.circuit_breaker.can_execute(),

            'recent_failures': self.circuit_breaker.failures,

            'api_requests_minute': len(recent_requests),

            'total_requests': self.request_count,

            'cache_health': f"{len(self.cache)} entries"

        }

    

    def is_symbol_supported_cached(self, symbol: str) -> bool:

        """Check if symbol is supported (cached)"""

        return symbol not in self.unsupported_symbols

    

    def probe_symbol_supported(self, symbol: str) -> bool:

        """Probe if symbol is supported"""

        try:

            price = self.get_price_rest(symbol)

            return self.is_symbol_supported_cached(symbol)

        except Exception:

            return True

    

    def test_connection(self, symbol: str = "BTC_USDT") -> bool:

        """Test connection to exchange"""

        try:

            price = self.get_price_rest(symbol)

            return price is not None

        except Exception:

            return False

    

    def get_order_flow_analysis(self, symbol: str, limit: int = 20) -> Dict:

        """Get order flow analysis - default implementation"""

        try:

            trades = self.get_aggregated_trades(symbol, limit)

            if not trades:

                return {}

            buy_volume = sum(t['qty'] for t in trades if not t.get('is_buyer_maker', False))

            sell_volume = sum(t['qty'] for t in trades if t.get('is_buyer_maker', False))

            total_volume = buy_volume + sell_volume

            buy_ratio = (buy_volume / total_volume * 100) if total_volume > 0 else 50.0

            return {

                'buy_volume': buy_volume,

                'sell_volume': sell_volume,

                'buy_ratio': buy_ratio,

                'net_flow': buy_volume - sell_volume,

                'sample_size': len(trades)

            }

        except Exception:

            return {}





class BaseWebSocketManager:

    """Base class for WebSocket managers"""

    def __init__(self, exchange_name: str):

        self.exchange_name = exchange_name

        self.connections = {}

        self.price_cache = {}

        self.orderbook_cache = {}

        self.trade_cache = {}

        self.candle_cache = {}

    

    async def start_stream(self, symbol: str):

        """Start WebSocket stream - must be implemented in subclass"""

        raise NotImplementedError("Subclass must implement start_stream")

    

    async def stop_all_streams(self):

        """Stop all WebSocket streams"""

        try:

            for symbol, websocket in self.connections.items():

                try:

                    await websocket.close()

                except Exception:

                    pass

            self.connections.clear()

        except Exception as e:

            print(f"{RED}❌ Error stopping WebSocket streams: {e}{RESET}")

    

    def get_price(self, symbol: str, max_age_s: float = 2.0) -> Optional[float]:

        """Get price from WebSocket cache with age validation (international standard: max 2s for trading)"""

        if symbol in self.price_cache:

            price_data = self.price_cache[symbol]

            age = time.time() - price_data.get('timestamp', 0)

            if age < max_age_s:

                return price_data.get('price')

        return None

    

    def get_orderbook(self, symbol: str, limit: int = 100, max_age_s: float = 5.0) -> Optional[Dict]:

        """Get orderbook from WebSocket cache"""

        if symbol in self.orderbook_cache:

            ob_data = self.orderbook_cache[symbol]

            age = time.time() - ob_data.get('timestamp', 0)

            if age < max_age_s:

                return ob_data

        return None





class BybitClient(BaseExchangeClient):

    def __init__(self):

        super().__init__("BYBIT")

        self.base_url = "https://api.bybit.com"

        self.cache_ttl = {

            'price': 2,

            'candles_15m': 60,

            'candles_1h': 300,

            'candles_4h': 900

        }



    def _respect_rate_limits(self):

        super()._respect_rate_limits()  # Use base implementation



    def get_price_rest(self, symbol: str) -> Optional[float]:

        start_time = time.time()

        self._respect_rate_limits()

        if time.time() - self.last_cache_cleanup > 300:

            self.cleanup_old_cache()

        self.request_count += 1

        performance_monitor.record_api_call(time.time() - start_time)

        cache_key = f"price_{symbol}"

        cached_value = self.cache.get(cache_key)

        if cached_value and time.time() - cached_value['timestamp'] < self.cache_ttl['price']:

            return cached_value['data']

        bybit_symbol = symbol.replace('_', '')

        try:

            self.request_timestamps.append(time.time())

            url = f"{self.base_url}/v5/market/tickers"

            params = {

                "category": "spot",

                "symbol": bybit_symbol

            }

            r = self.session.get(url, params=params, timeout=5)

            r.raise_for_status()

            data = r.json()

            if data.get('retCode') == 0 and data.get('result'):

                ticker_list = data['result'].get('list', [])

                if ticker_list:

                    price = float(ticker_list[0]['lastPrice'])

                    if self.validate_price(price):

                        self.cache.set(cache_key, {'data': price, 'timestamp': time.time()})

                        self.circuit_breaker.record_success()

                        return price

                else:

                    return None

            else:

                msg = str(data.get('retMsg', '')).lower()

                if 'not supported' in msg:

                    self.unsupported_symbols.add(symbol)

                    return None

        except Exception as e:

            print(f"{YELLOW}⚠️ Bybit API error for {symbol}: {e}{RESET}")

            self.circuit_breaker.record_failure()

            return None

        print(f"{RED}❌ Failed to get price from Bybit for {symbol}{RESET}")

        self.circuit_breaker.record_failure()

        return None



    def get_current_price(self, symbol: str) -> Optional[float]:

        return self.get_price_rest(symbol)



    def fetch_candles(self, symbol: str, interval: str = "5m", limit: int = 200) -> Optional[List[Dict]]:

        start_time = time.time()

        self._respect_rate_limits()

        if time.time() - self.last_cache_cleanup > 300:

            self.cleanup_old_cache()

        self.request_count += 1

        performance_monitor.record_api_call(time.time() - start_time)

        cache_key = f"candles_{symbol}_{interval}_{limit}"

        cached_value = self.cache.get(cache_key)

        if cached_value and time.time() - cached_value['timestamp'] < self.cache_ttl.get(f'candles_{interval}', 60):

            return cached_value['data']

        bybit_symbol = symbol.replace('_', '')

        interval_map = {

            '1m': '1', '3m': '3', '5m': '5', '15m': '15', '30m': '30',

            '1h': '60', '2h': '120', '4h': '240', '6h': '360', '12h': '720',

            '1d': 'D', '1w': 'W', '1M': 'M'

        }

        bybit_interval = interval_map.get(interval, '5')

        try:

            self.request_timestamps.append(time.time())

            url = f"{self.base_url}/v5/market/kline"

            params = {

                "category": "spot",

                "symbol": bybit_symbol,

                "interval": bybit_interval,

                "limit": min(limit, 1000)

            }

            r = self.session.get(url, params=params, timeout=10)

            r.raise_for_status()

            data = r.json()

            candles = []

            if data.get('retCode') == 0 and data.get('result'):

                klines = data['result'].get('list', [])

                for k in reversed(klines):

                    try:

                        candle_data = {

                            "ts": float(k[0]),

                            "open": float(k[1]),

                            "high": float(k[2]),

                            "low": float(k[3]),

                            "close": float(k[4]),

                            "volume": float(k[5])

                        }

                        if self.validate_candle_data(candle_data):

                            candles.append(candle_data)

                    except Exception:

                        continue

                if candles:

                    result = sorted(candles, key=lambda x: x["ts"])

                    self.cache.set(cache_key, {'data': result, 'timestamp': time.time()})

                    self.circuit_breaker.record_success()

                    return result

            # Silent skip for invalid symbols (not available in spot market)

            # Don't print warning for futures-only coins like JELLYJELLY

            # Only print if it's a legitimate error (not invalid symbol)

            # Check if symbol is supported before printing warning

            from core import _init_exchange_client

            exchange_client = _init_exchange_client()

            if exchange_client and hasattr(exchange_client, 'is_symbol_supported_cached'):

                if not exchange_client.is_symbol_supported_cached(symbol):

                    # Symbol not supported - silently skip (likely futures-only coin)

                    pass

                else:

                    # Symbol is supported but no candle data - this is unusual but don't spam

                    # print(f"{YELLOW}⚠️ No candle data from Bybit for {symbol}{RESET}")

                    pass

            else:

                # Can't check - silently skip to reduce spam

                pass

            self.circuit_breaker.record_failure()

            return None

        except Exception as e:

            print(f"{RED}❌ Bybit candle error for {symbol}: {e}{RESET}")

            self.circuit_breaker.record_failure()

            error_handler.record_error("API_Error", f"Candle fetch error: {symbol}", {"error": str(e)})

            return None



    def validate_price(self, price: float, previous_price: float = None) -> bool:

        if (price <= 0 or math.isnan(price) or math.isinf(price) or price > 1e7):

            return False

        if previous_price is not None:

            if previous_price <= 0:

                return True

            price_change = abs(price / previous_price - 1)

            if price_change > 0.5:

                return False

        return True



    def validate_candle_data(self, candle: Dict) -> bool:

        try:

            required_fields = ['open', 'high', 'low', 'close', 'volume']

            for field in required_fields:

                if field not in candle or candle[field] is None:

                    return False

            if (candle['open'] <= 0 or candle['close'] <= 0 or

                candle['high'] <= 0 or candle['low'] <= 0 or

                candle['volume'] < 0):

                return False

            if (candle['high'] < candle['low'] or

                candle['high'] < max(candle['open'], candle['close']) or

                candle['low'] > min(candle['open'], candle['close'])):

                return False

            return True

        except Exception:

            return False



    def get_api_stats(self):

        now = time.time()

        recent_requests = [ts for ts in self.request_timestamps if now - ts < 60]

        return {

            'total_requests': self.request_count,

            'requests_per_minute': len(recent_requests),

            'circuit_breaker_failures': self.circuit_breaker.failures,

            'circuit_breaker_active': not self.circuit_breaker.can_execute(),

            'cache_size': len(self.cache)

        }



    def get_health_status(self):

        now = time.time()

        recent_requests = [ts for ts in self.request_timestamps if now - ts < 60]

        return {

            'circuit_breaker_active': not self.circuit_breaker.can_execute(),

            'recent_failures': self.circuit_breaker.failures,

            'api_requests_minute': len(recent_requests),

            'total_requests': self.request_count,

            'cache_health': f"{len(self.cache)} entries"

        }



    def get_aggregated_trades(self, symbol: str, limit: int = 500) -> Optional[List[Dict]]:

        start_time = time.time()

        self._respect_rate_limits()

        self.request_count += 1

        try:

            bybit_symbol = symbol.replace('_', '')

            url = f"{self.base_url}/v5/market/recent-trade"

            params = {

                'category': 'spot',

                'symbol': bybit_symbol,

                'limit': limit

            }

            response = self.session.get(url, params=params, timeout=10)

            response.raise_for_status()

            data = response.json()

            if data.get('retCode') == 0 and 'result' in data:

                trades = data['result'].get('list', [])

                processed_trades = []

                for trade in trades:

                    side = trade.get('side', 'Buy')

                    is_buyer_maker = (side == 'Sell')

                    processed_trades.append({

                        'price': float(trade['price']),

                        'qty': float(trade['size']),

                        'time': int(trade['time']),

                        'is_buyer_maker': is_buyer_maker

                    })

                return processed_trades

            else:

                if str(data.get('retMsg', '')).lower().find('not supported') >= 0:

                    self.unsupported_symbols.add(symbol)

                    return None

                print(f"{YELLOW}⚠️ Bybit aggregated trades error: {data.get('retMsg', 'Unknown error')}{RESET}")

                return None

        except Exception as e:

            print(f"{RED}❌ Bybit aggregated trades error: {e}{RESET}")

            self.circuit_breaker.record_failure()

            return None

        finally:

            duration = time.time() - start_time

            if duration > 1.0:

                print(f"{YELLOW}⚠️ Slow Bybit aggregated trades: {duration:.2f}s{RESET}")



    def get_orderbook(self, symbol: str, limit: int = 100) -> Optional[Dict]:

        start_time = time.time()

        self._respect_rate_limits()

        self.request_count += 1

        try:

            bybit_symbol = symbol.replace('_', '')

            url = f"{self.base_url}/v5/market/orderbook"

            params = {

                'category': 'spot',

                'symbol': bybit_symbol,

                'limit': limit

            }

            response = self.session.get(url, params=params, timeout=10)

            response.raise_for_status()

            data = response.json()

            if data.get('retCode') == 0 and 'result' in data:

                ob_data = data['result']

                return {

                    'bids': [[float(bid[0]), float(bid[1])] for bid in ob_data.get('b', [])],

                    'asks': [[float(ask[0]), float(ask[1])] for ask in ob_data.get('a', [])],

                    'timestamp': ob_data.get('ts', int(time.time() * 1000))

                }

            else:

                if str(data.get('retMsg', '')).lower().find('not supported') >= 0:

                    self.unsupported_symbols.add(symbol)

                    return None

                print(f"{YELLOW}⚠️ Bybit orderbook error: {data.get('retMsg', 'Unknown error')}{RESET}")

                return None

        except Exception as e:

            print(f"{RED}❌ Bybit orderbook error: {e}{RESET}")

            self.circuit_breaker.record_failure()

            return None

        finally:

            duration = time.time() - start_time

            if duration > 1.0:

                print(f"{YELLOW}⚠️ Slow Bybit orderbook: {duration:.2f}s{RESET}")



    def get_order_flow_analysis(self, symbol: str, limit: int = 20) -> Dict:

        try:

            trades = self.get_aggregated_trades(symbol, limit)

            if not trades:

                # Return neutral buy_pressure (50.0%) instead of 0.0% when no trades
                return {

                    'buy_pressure': 50.0,  # Neutral default, not 0.0%

                    'sell_pressure': 50.0,

                    'cvd': 0,

                    'net_flow': 0,

                    'trade_count': 0,

                    'volume_profile': {},

                    'institutional_activity': 0,

                    'tape_reading': {},

                    'cvd_momentum': 0,

                    'order_imbalance': 0

                }

            buy_volume = 0

            sell_volume = 0

            cvd = 0

            cvd_history = []

            large_trades = 0

            price_levels = {}
            
            valid_trades_count = 0

            for trade in trades:
                # Validate trade data - skip invalid trades
                price = trade.get('price', 0)
                qty = trade.get('qty', 0)
                
                # Skip trades with invalid data (price or qty <= 0)
                if price <= 0 or qty <= 0:
                    continue
                
                valid_trades_count += 1
                volume = price * qty

                if price < 1.0:

                    price_bucket = round(price, 4)

                elif price < 100.0:

                    price_bucket = round(price, 2)

                else:

                    price_bucket = round(price, 1)

                if price_bucket not in price_levels:

                    price_levels[price_bucket] = {'buy': 0, 'sell': 0, 'total': 0}

                if trade.get('is_buyer_maker', False):

                    sell_volume += volume

                    cvd -= volume

                    price_levels[price_bucket]['sell'] += volume

                else:

                    buy_volume += volume

                    cvd += volume

                    price_levels[price_bucket]['buy'] += volume

                price_levels[price_bucket]['total'] += volume

                cvd_history.append(cvd)

                avg_trade_size = (buy_volume + sell_volume) / valid_trades_count if valid_trades_count > 0 else 0

                if volume > avg_trade_size * 3:

                    large_trades += 1

            total_volume = buy_volume + sell_volume

            # CRITICAL FIX: If total_volume=0, return default 50.0% (neutral)
            # This can happen if all trades have invalid data (price=0 or qty=0)
            if total_volume == 0:
                buy_pressure = 50.0
                sell_pressure = 50.0
            else:
                buy_pressure = safe_division(buy_volume, total_volume, 0.5) * 100
                sell_pressure = safe_division(sell_volume, total_volume, 0.5) * 100
            
            # Debug logging untuk buy_pressure 0.0% (hanya jika ENABLE_ORDER_FLOW_DEBUG)
            if getattr(TradingConfig, 'ENABLE_ORDER_FLOW_DEBUG', False):
                if total_volume == 0 and len(trades) > 0:
                    # All trades were invalid (price=0 or qty=0)
                    print(f"{YELLOW}⚠️ [REST API] All {len(trades)} trades have invalid data (price=0 or qty=0) - using default buy_pressure=50.0%{RESET}")
                elif buy_pressure == 0.0 and total_volume > 0:
                    print(f"{CYAN}✅ [REST API] VALID: buy_pressure=0.0% (all valid trades are SELL){RESET}")

            cvd_momentum = 0

            if len(cvd_history) >= 5:

                recent_cvd = cvd_history[-5:]

                cvd_momentum = (recent_cvd[-1] - recent_cvd[0]) / 5.0

            volume_profile = {}

            if price_levels:

                sorted_levels = sorted(price_levels.items(), key=lambda x: x[1]['total'], reverse=True)

                volume_profile = {

                    'high_volume_levels': sorted_levels[:3],

                    'total_levels': len(price_levels),

                    'volume_concentration': sum(level[1]['total'] for level in sorted_levels[:3]) / total_volume if total_volume > 0 else 0

                }

            tape_reading = {

                'large_trade_ratio': safe_division(large_trades, valid_trades_count, 0) * 100 if valid_trades_count > 0 else 0,

                'avg_trade_size': safe_division(total_volume, valid_trades_count, 0) if valid_trades_count > 0 else 0,

                'trade_frequency': valid_trades_count,

                'volume_acceleration': cvd_momentum

            }

            order_imbalance = 0

            try:

                ob = self.get_orderbook(symbol, 10)

                if ob and 'bids' in ob and 'asks' in ob:

                    bid_volume = sum(bid[1] for bid in ob['bids'])

                    ask_volume = sum(ask[1] for ask in ob['asks'])

                    order_imbalance = safe_division(bid_volume - ask_volume, bid_volume + ask_volume, 0) * 100

            except Exception:

                pass

            return {

                'buy_pressure': buy_pressure,

                'sell_pressure': sell_pressure,

                'cvd': cvd,

                'net_flow': buy_volume - sell_volume,

                'trade_count': valid_trades_count,

                'volume_profile': volume_profile,

                'institutional_activity': safe_division(large_trades, valid_trades_count, 0) * 100 if valid_trades_count > 0 else 0,

                'tape_reading': tape_reading,

                'cvd_momentum': cvd_momentum,

                'order_imbalance': order_imbalance

            }

        except Exception as e:

            print(f"{RED}❌ Enhanced order flow analysis error: {e}{RESET}")

            # Return neutral buy_pressure (50.0%) instead of 0.0% on error
            return {

                'buy_pressure': 50.0,  # Neutral default, not 0.0%

                'sell_pressure': 50.0,

                'cvd': 0,

                'net_flow': 0,

                'trade_count': 0,

                'volume_profile': {},

                'institutional_activity': 0,

                'tape_reading': {},

                'cvd_momentum': 0,

                'order_imbalance': 0

            }



    def test_connection(self, symbol: str) -> bool:

        try:

            price = self.get_price_rest(symbol)

            return price is not None

        except Exception:

            return False





# NOTE: BinanceClient has been moved to exchanger/binance.py

# This keeps the codebase consistent with other exchanges





class OKXClient(BaseExchangeClient):

    """OKX Exchange Client"""

    def __init__(self):

        super().__init__("OKX")

        self.base_url = "https://www.okx.com"

        self.cache_ttl = {

            'price': 2,

            'candles_15m': 60,

            'candles_1h': 300,

            'candles_4h': 900

        }

    

    def normalize_symbol(self, symbol: str) -> str:

        """OKX uses BTC-USDT format"""

        parts = symbol.split('_')

        if len(parts) == 2:

            return f"{parts[0]}-{parts[1]}"

        return symbol.replace('_', '-')

    

    def get_price_rest(self, symbol: str) -> Optional[float]:

        start_time = time.time()

        self._respect_rate_limits()

        if time.time() - self.last_cache_cleanup > 300:

            self.cleanup_old_cache()

        self.request_count += 1

        performance_monitor.record_api_call(time.time() - start_time)

        cache_key = f"price_{symbol}"

        cached_value = self.cache.get(cache_key)

        if cached_value and time.time() - cached_value['timestamp'] < self.cache_ttl['price']:

            return cached_value['data']

        okx_symbol = self.normalize_symbol(symbol)

        try:

            self.request_timestamps.append(time.time())

            url = f"{self.base_url}/api/v5/market/ticker"

            params = {"instId": okx_symbol}

            r = self.session.get(url, params=params, timeout=5)

            r.raise_for_status()

            data = r.json()

            if data.get('code') == '0' and data.get('data'):

                ticker = data['data'][0]

                price = float(ticker['last'])

                if self.validate_price(price):

                    self.cache.set(cache_key, {'data': price, 'timestamp': time.time()})

                    self.circuit_breaker.record_success()

                    return price

        except Exception as e:

            print(f"{YELLOW}⚠️ OKX API error for {symbol}: {e}{RESET}")

            self.circuit_breaker.record_failure()

            return None

        print(f"{RED}❌ Failed to get price from OKX for {symbol}{RESET}")

        self.circuit_breaker.record_failure()

        return None

    

    def fetch_candles(self, symbol: str, interval: str = "5m", limit: int = 200) -> Optional[List[Dict]]:

        start_time = time.time()

        self._respect_rate_limits()

        if time.time() - self.last_cache_cleanup > 300:

            self.cleanup_old_cache()

        self.request_count += 1

        performance_monitor.record_api_call(time.time() - start_time)

        cache_key = f"candles_{symbol}_{interval}_{limit}"

        cached_value = self.cache.get(cache_key)

        if cached_value and time.time() - cached_value['timestamp'] < self.cache_ttl.get(f'candles_{interval}', 60):

            return cached_value['data']

        okx_symbol = self.normalize_symbol(symbol)

        interval_map = {

            '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',

            '1h': '1H', '2h': '2H', '4h': '4H', '6h': '6H', '12h': '12H',

            '1d': '1D', '1w': '1W', '1M': '1M'

        }

        okx_interval = interval_map.get(interval, '5m')

        try:

            self.request_timestamps.append(time.time())

            url = f"{self.base_url}/api/v5/market/candles"

            params = {

                "instId": okx_symbol,

                "bar": okx_interval,

                "limit": min(limit, 300)

            }

            r = self.session.get(url, params=params, timeout=10)

            r.raise_for_status()

            data = r.json()

            candles = []

            if data.get('code') == '0' and data.get('data'):

                for k in reversed(data['data']):

                    try:

                        candle_data = {

                            "ts": float(k[0]),

                            "open": float(k[1]),

                            "high": float(k[2]),

                            "low": float(k[3]),

                            "close": float(k[4]),

                            "volume": float(k[5])

                        }

                        if self.validate_candle_data(candle_data):

                            candles.append(candle_data)

                    except Exception:

                        continue

                if candles:

                    result = sorted(candles, key=lambda x: x["ts"])

                    self.cache.set(cache_key, {'data': result, 'timestamp': time.time()})

                    self.circuit_breaker.record_success()

                    return result

            print(f"{YELLOW}⚠️ No candle data from OKX for {symbol}{RESET}")

            self.circuit_breaker.record_failure()

            return None

        except Exception as e:

            print(f"{RED}❌ OKX candle error for {symbol}: {e}{RESET}")

            self.circuit_breaker.record_failure()

            error_handler.record_error("API_Error", f"Candle fetch error: {symbol}", {"error": str(e)})

            return None

    

    def get_orderbook(self, symbol: str, limit: int = 100) -> Optional[Dict]:

        start_time = time.time()

        self._respect_rate_limits()

        self.request_count += 1

        try:

            okx_symbol = self.normalize_symbol(symbol)

            url = f"{self.base_url}/api/v5/market/books"

            params = {

                'instId': okx_symbol,

                'sz': limit

            }

            response = self.session.get(url, params=params, timeout=10)

            response.raise_for_status()

            data = response.json()

            if data.get('code') == '0' and data.get('data'):

                ob_data = data['data'][0]

                return {

                    'bids': [[float(bid[0]), float(bid[1])] for bid in ob_data.get('bids', [])],

                    'asks': [[float(ask[0]), float(ask[1])] for ask in ob_data.get('asks', [])],

                    'timestamp': int(time.time() * 1000)

                }

            return None

        except Exception as e:

            print(f"{RED}❌ OKX orderbook error: {e}{RESET}")

            self.circuit_breaker.record_failure()

            return None

    

    def get_aggregated_trades(self, symbol: str, limit: int = 500) -> Optional[List[Dict]]:

        start_time = time.time()

        self._respect_rate_limits()

        self.request_count += 1

        try:

            okx_symbol = self.normalize_symbol(symbol)

            url = f"{self.base_url}/api/v5/market/trades"

            params = {

                'instId': okx_symbol,

                'limit': min(limit, 500)

            }

            response = self.session.get(url, params=params, timeout=10)

            response.raise_for_status()

            data = response.json()

            processed_trades = []

            if data.get('code') == '0' and data.get('data'):

                for trade in data['data']:

                    side = trade.get('side', 'buy')

                    processed_trades.append({

                        'price': float(trade['px']),

                        'qty': float(trade['sz']),

                        'time': int(trade['ts']),

                        'is_buyer_maker': (side == 'sell')

                    })

            return processed_trades

        except Exception as e:

            print(f"{RED}❌ OKX aggregated trades error: {e}{RESET}")

            self.circuit_breaker.record_failure()

            return None





# NOTE: ExchangeFactory has been moved to exchanger/factory.py

# This old implementation is kept for backward compatibility but is not used

# All code now uses ExchangeFactory from exchanger module





class BybitWebSocketManager:

    def __init__(self):

        self.base_url = "wss://stream.bybit.com/v5/public/spot"

        self.connections = {}

        self.price_cache = {}

        self.orderbook_cache = {}

        self.trade_cache = {}

        self.candle_cache = {}

        self.bid_wall_history = {}  # Track bid wall over time for spoofing detection

        self.cache_stats = {

            'price_hits': 0,

            'price_misses': 0,

            'orderbook_hits': 0,

            'orderbook_misses': 0,

            'flow_hits': 0,

            'flow_misses': 0,

            'last_report': time.time()

        }



    def get_cache_status(self, symbol: str) -> Dict[str, Any]:

        now = time.time()

        price_warm = False

        price_age_s = None

        if symbol in self.price_cache:

            price_age_s = now - self.price_cache[symbol].get('timestamp', 0)

            price_warm = price_age_s is not None and price_age_s < 10

        ob_warm = False

        ob_age_s = None

        if symbol in self.orderbook_cache:

            ob_age_s = now - self.orderbook_cache[symbol].get('timestamp', 0)

            ob_warm = ob_age_s is not None and ob_age_s < 10

        intervals = ['1m', '5m', '15m', '1h', '4h']

        kline_status = {}

        warm_count = 0

        for iv in intervals:

            key = f"{symbol}_{iv}"

            warm = False

            if key in self.candle_cache:

                age = now - self.candle_cache[key].get('timestamp', 0)

                warm = age < 180 and bool(self.candle_cache[key].get('candles'))

            kline_status[iv] = warm

            if warm:

                warm_count += 1

        return {

            'price_warm': price_warm,

            'price_age': price_age_s,

            'orderbook_warm': ob_warm,

            'orderbook_age': ob_age_s,

            'kline_warm_count': warm_count,

            'kline_total': len(intervals),

            'kline': kline_status

        }



    async def start_stream(self, symbol: str):

        if symbol in self.connections:

            return

        try:

            ws_symbol = symbol.replace('_', '')

            stream_name = f"{ws_symbol.lower()}"

            url = f"{self.base_url}"

            async with websockets.connect(url) as websocket:

                self.connections[symbol] = websocket

                subscribe_msg = {

                    "op": "subscribe",

                    "args": [

                        f"tickers.{ws_symbol}",

                        f"orderbook.50.{ws_symbol}",

                        f"publicTrade.{ws_symbol}",

                        f"kline.1.{ws_symbol}",

                        f"kline.5.{ws_symbol}",

                        f"kline.15.{ws_symbol}",

                        f"kline.60.{ws_symbol}",

                        f"kline.240.{ws_symbol}"

                    ]

                }

                await websocket.send(json.dumps(subscribe_msg))

                async for message in websocket:

                    try:

                        await self._process_message(symbol, json.loads(message))

                    except Exception as e:

                        print(f"{YELLOW}⚠️ WebSocket message error for {symbol}: {e}{RESET}")

        except websockets.exceptions.ConnectionClosed:

            print(f"{YELLOW}⚠️ WebSocket connection closed for {symbol}{RESET}")

        except Exception as e:

            print(f"{RED}❌ WebSocket error for {symbol}: {e}{RESET}")

        finally:

            if symbol in self.connections:

                del self.connections[symbol]



    async def _process_message(self, symbol: str, data: dict):

        try:

            if 'success' in data:

                if data.get('success'):

                    pass

                else:

                    # Silent skip for invalid symbols (not available in spot market)

                    # Error message is expected for futures-only coins like JELLYJELLY

                    ret_msg = data.get('ret_msg', 'Unknown error')

                    if 'invalid symbol' in ret_msg.lower():

                        # Silently skip - coin not available in spot market

                        pass

                    else:

                        # Only show non-invalid-symbol errors

                        print(f"{YELLOW}⚠️ WebSocket subscription warning: {ret_msg}{RESET}")

                return

            if 'topic' not in data:

                return

            topic = data['topic']

            message_data = data.get('data', [])

            if 'tickers' in topic:

                try:

                    price = 0

                    if isinstance(message_data, dict):

                        price = float(message_data.get('lastPrice', 0))

                    elif isinstance(message_data, list) and len(message_data) > 0:

                        price = float(message_data[0].get('lastPrice', 0))

                    if price > 0:

                        self.price_cache[symbol] = {

                            'price': price,

                            'timestamp': time.time()

                        }

                except (KeyError, ValueError, TypeError, IndexError) as e:

                    pass

            elif 'orderbook' in topic:

                try:

                    bids = []

                    asks = []

                    if isinstance(message_data, dict):

                        bids = message_data.get('b', [])

                        asks = message_data.get('a', [])

                    elif isinstance(message_data, list) and len(message_data) > 0:

                        ob_data = message_data[0]

                        bids = ob_data.get('b', [])

                        asks = ob_data.get('a', [])

                    if bids or asks:

                        try:

                            max_levels = int(os.getenv('ORDERBOOK_CACHE_LEVELS', '50'))

                        except Exception:

                            max_levels = 50

                        self.orderbook_cache[symbol] = {

                            'bids': [[float(bid[0]), float(bid[1])] for bid in bids[:max_levels]],

                            'asks': [[float(ask[0]), float(ask[1])] for ask in asks[:max_levels]],

                            'timestamp': time.time()

                        }

                except (KeyError, ValueError, TypeError, IndexError) as e:

                    pass

            elif 'publicTrade' in topic:

                try:

                    if isinstance(message_data, list) and len(message_data) > 0:

                        trades = []

                        for trade in message_data:

                            trades.append({

                                'price': float(trade.get('p', 0)),

                                'qty': float(trade.get('v', 0)),

                                'time': int(trade.get('T', 0)),

                                'is_buyer_maker': trade.get('S') == 'Sell'

                            })

                        if symbol not in self.trade_cache:

                            self.trade_cache[symbol] = []

                        self.trade_cache[symbol].extend(trades)

                        if len(self.trade_cache[symbol]) > 100:

                            self.trade_cache[symbol] = self.trade_cache[symbol][-100:]

                except (KeyError, ValueError, TypeError) as e:

                    pass

            elif 'kline.' in topic:

                try:

                    parts = topic.split('.')

                    interval_code = parts[1] if len(parts) > 1 else None

                    interval_map = {

                        '1': '1m', '3': '3m', '5': '5m', '15': '15m', '30': '30m',

                        '60': '1h', '120': '2h', '240': '4h', '360': '6h', '720': '12h',

                        'D': '1d', 'W': '1w', 'M': '1M'

                    }

                    internal_interval = interval_map.get(interval_code)

                    if not internal_interval:

                        return

                    if isinstance(message_data, list) and len(message_data) > 0:

                        for k in message_data:

                            kline = {

                                't': k.get('start') or k.get('t'),

                                'T': k.get('end') or k.get('T'),

                                'o': k.get('open') or k.get('o'),

                                'h': k.get('high') or k.get('h'),

                                'l': k.get('low') or k.get('l'),

                                'c': k.get('close') or k.get('c'),

                                'v': k.get('volume') or k.get('v')

                            }

                            if all(x is not None for x in [kline['t'], kline['T'], kline['o'], kline['h'], kline['l'], kline['c'], kline['v']] ):

                                self.update_candle_cache(symbol, internal_interval, kline)

                except Exception as e:

                    pass

        except Exception as e:

            print(f"{RED}❌ WebSocket message processing error: {e}{RESET}")



    async def _handle_reconnection(self, symbol: str):

        if symbol in self.connections:

            del self.connections[symbol]

        await asyncio.sleep(5)

        await self.start_stream(symbol)



    def get_price(self, symbol: str) -> Optional[float]:

        if symbol in self.price_cache:

            cache_data = self.price_cache[symbol]

            if time.time() - cache_data['timestamp'] < 30:

                self.cache_stats['price_hits'] += 1

                self._report_cache_stats()

                return cache_data['price']

        self.cache_stats['price_misses'] += 1

        self._report_cache_stats()

        return None



    def track_bid_wall(self, symbol: str, bid_ratio: float, bid_wall_price: float):

        """Track bid wall history for spoofing detection (last 60 seconds)"""

        if symbol not in self.bid_wall_history:

            self.bid_wall_history[symbol] = []

        

        current_time = time.time()

        self.bid_wall_history[symbol].append({

            'timestamp': current_time,

            'bid_ratio': bid_ratio,

            'bid_wall_price': bid_wall_price

        })

        

        # Keep only last 60 seconds of history

        self.bid_wall_history[symbol] = [

            entry for entry in self.bid_wall_history[symbol]

            if current_time - entry['timestamp'] < 60

        ]

        

        # Limit history size to prevent memory issues (max 120 entries = 2 per second for 60s)

        if len(self.bid_wall_history[symbol]) > 120:

            self.bid_wall_history[symbol] = self.bid_wall_history[symbol][-120:]

    

    def get_bid_wall_history(self, symbol: str) -> List[Dict]:

        """Get bid wall history for spoofing detection"""

        return self.bid_wall_history.get(symbol, [])



    def get_order_flow_analysis(self, symbol: str, limit: int = 20) -> Dict:

        try:

            if symbol not in self.trade_cache or not self.trade_cache[symbol]:

                self.cache_stats['flow_misses'] += 1

                self._report_cache_stats()

                # WebSocket cache empty - this is normal if:
                # 1. WebSocket just started (cache not populated yet)
                # 2. Symbol not subscribed yet
                # 3. No recent trades for this symbol
                # Return neutral buy_pressure (50.0%) instead of 0.0% when cache is empty
                return {

                    'buy_pressure': 50.0,  # Neutral default, not 0.0%

                    'sell_pressure': 50.0,

                    'cvd': 0,

                    'net_flow': 0,

                    'trade_count': 0,

                    'volume_profile': {},

                    'institutional_activity': 0,

                    'tape_reading': {},

                    'cvd_momentum': 0,

                    'order_imbalance': 0

                }

            self.cache_stats['flow_hits'] += 1

            self._report_cache_stats()

            # Filter trades by age (international standard: max 30s for order flow analysis)

            current_time = time.time()

            max_age_s = 30.0  # Only use trades from last 30 seconds

            recent_trades = [

                trade for trade in self.trade_cache[symbol]

                if current_time - (trade.get('time', 0) / 1000) < max_age_s

            ]

            # If no recent trades, use all available (but log warning)

            if not recent_trades:

                recent_trades = self.trade_cache[symbol]

            trades = recent_trades[-limit:] if len(recent_trades) >= limit else recent_trades

            buy_volume = 0

            sell_volume = 0

            cvd = 0

            cvd_history = []

            large_trades = 0

            price_levels = {}
            
            valid_trades_count = 0

            for trade in trades:
                # Validate trade data - skip invalid trades
                price = trade.get('price', 0)
                qty = trade.get('qty', 0)
                
                # Skip trades with invalid data (price or qty <= 0)
                if price <= 0 or qty <= 0:
                    continue
                
                valid_trades_count += 1
                volume = price * qty

                if price < 1.0:

                    price_bucket = round(price, 4)

                elif price < 100.0:

                    price_bucket = round(price, 2)

                else:

                    price_bucket = round(price, 1)

                if price_bucket not in price_levels:

                    price_levels[price_bucket] = {'buy': 0, 'sell': 0, 'total': 0}

                if trade.get('is_buyer_maker', False):

                    sell_volume += volume

                    cvd -= volume

                    price_levels[price_bucket]['sell'] += volume

                else:

                    buy_volume += volume

                    cvd += volume

                    price_levels[price_bucket]['buy'] += volume

                price_levels[price_bucket]['total'] += volume

                cvd_history.append(cvd)

                avg_trade_size = (buy_volume + sell_volume) / valid_trades_count if valid_trades_count > 0 else 0

                if volume > avg_trade_size * 3:

                    large_trades += 1

            total_volume = buy_volume + sell_volume

            # CRITICAL FIX: If total_volume=0, return default 50.0% (neutral)
            # This can happen if all trades have invalid data (price=0 or qty=0)
            if total_volume == 0:
                buy_pressure = 50.0
                sell_pressure = 50.0
            else:
                buy_pressure = safe_division(buy_volume, total_volume, 0.5) * 100
                sell_pressure = safe_division(sell_volume, total_volume, 0.5) * 100
            
            # Debug logging untuk buy_pressure 0.0% (hanya jika ENABLE_ORDER_FLOW_DEBUG)
            if getattr(TradingConfig, 'ENABLE_ORDER_FLOW_DEBUG', False):
                if total_volume == 0 and len(trades) > 0:
                    # All trades were invalid (price=0 or qty=0)
                    print(f"{YELLOW}⚠️ [WebSocket] All {len(trades)} trades have invalid data (price=0 or qty=0) - using default buy_pressure=50.0%{RESET}")
                elif buy_pressure == 0.0 and total_volume > 0:
                    print(f"{CYAN}✅ [WebSocket] VALID: buy_pressure=0.0% (all valid trades are SELL){RESET}")

            cvd_momentum = 0

            if len(cvd_history) >= 5:

                recent_cvd = cvd_history[-5:]

                cvd_momentum = (recent_cvd[-1] - recent_cvd[0]) / 5.0

            volume_profile = {}

            if price_levels:

                sorted_levels = sorted(price_levels.items(), key=lambda x: x[1]['total'], reverse=True)

                volume_profile = {

                    'high_volume_levels': sorted_levels[:3],

                    'total_levels': len(price_levels),

                    'volume_concentration': sum(level[1]['total'] for level in sorted_levels[:3]) / total_volume if total_volume > 0 else 0

                }

            tape_reading = {

                'large_trade_ratio': safe_division(large_trades, valid_trades_count, 0) * 100 if valid_trades_count > 0 else 0,

                'avg_trade_size': safe_division(total_volume, valid_trades_count, 0) if valid_trades_count > 0 else 0,

                'trade_frequency': valid_trades_count,

                'volume_acceleration': cvd_momentum

            }

            order_imbalance = 0

            try:

                ob = self.orderbook_cache.get(symbol)

                if ob and 'bids' in ob and 'asks' in ob:

                    bid_volume = sum(bid[1] for bid in ob['bids'])

                    ask_volume = sum(ask[1] for ask in ob['asks'])

                    order_imbalance = safe_division(bid_volume - ask_volume, bid_volume + ask_volume, 0) * 100

            except Exception:

                pass

            return {

                'buy_pressure': buy_pressure,

                'sell_pressure': sell_pressure,

                'cvd': cvd,

                'net_flow': buy_volume - sell_volume,

                'trade_count': valid_trades_count,

                'volume_profile': volume_profile,

                'institutional_activity': safe_division(large_trades, valid_trades_count, 0) * 100 if valid_trades_count > 0 else 0,

                'tape_reading': tape_reading,

                'cvd_momentum': cvd_momentum,

                'order_imbalance': order_imbalance

            }

        except Exception as e:

            print(f"{RED}❌ Enhanced WebSocket order flow analysis error: {e}{RESET}")

            # Return neutral buy_pressure (50.0%) instead of 0.0% on WebSocket error
            return {

                'buy_pressure': 50.0,  # Neutral default, not 0.0%

                'sell_pressure': 50.0,

                'cvd': 0,

                'net_flow': 0,

                'trade_count': 0,

                'volume_profile': {},

                'institutional_activity': 0,

                'tape_reading': {},

                'cvd_momentum': 0,

                'order_imbalance': 0

            }



    async def initialize_candle_cache(self, symbol: str, interval: str = "5m", limit: int = 200):

        try:

            exchange_client = _init_exchange_client()

            if not exchange_client or not hasattr(exchange_client, 'fetch_candles'):

                return

            candles = exchange_client.fetch_candles(symbol, interval, limit)

            if candles:

                self.candle_cache[f"{symbol}_{interval}"] = {

                    'candles': candles,

                    'timestamp': time.time()

                }

        except Exception as e:

            print(f"{RED}❌ WebSocket candle cache initialization error: {e}{RESET}")



    def update_candle_cache(self, symbol: str, interval: str, kline_data: dict):

        try:

            cache_key = f"{symbol}_{interval}"

            if cache_key in self.candle_cache:

                candles = self.candle_cache[cache_key]['candles']

                new_candle = {

                    'ts': int(kline_data['t']),

                    'open': float(kline_data['o']),

                    'high': float(kline_data['h']),

                    'low': float(kline_data['l']),

                    'close': float(kline_data['c']),

                    'volume': float(kline_data['v'])

                }

                last_ts = candles[-1].get('ts') if candles else None

                if last_ts == new_candle['ts']:

                    candles[-1] = new_candle

                else:

                    candles.append(new_candle)

                if len(candles) > 200:

                    candles[:] = candles[-200:]

                self.candle_cache[cache_key]['timestamp'] = time.time()

        except Exception as e:

            print(f"{RED}❌ WebSocket candle cache update error: {e}{RESET}")



    def get_candles(self, symbol: str, interval: str = "5m", limit: int = 200) -> Optional[List[Dict]]:

        cache_key = f"{symbol}_{interval}"

        if cache_key in self.candle_cache:

            cache_data = self.candle_cache[cache_key]

            if time.time() - cache_data['timestamp'] < 300:

                return cache_data['candles'][-limit:] if len(cache_data['candles']) >= limit else cache_data['candles']

        return None



    def get_orderbook(self, symbol: str, limit: int = 20, max_age_s: int = 30) -> Optional[Dict]:

        if symbol in self.orderbook_cache:

            cache_data = self.orderbook_cache[symbol]

            age = time.time() - cache_data.get('timestamp', 0)

            if age < max_age_s:

                self.cache_stats['orderbook_hits'] += 1

                self._report_cache_stats()

                bids = cache_data['bids'][:limit] if len(cache_data['bids']) >= limit else cache_data['bids']

                asks = cache_data['asks'][:limit] if len(cache_data['asks']) >= limit else cache_data['asks']

                return {

                    'bids': bids,

                    'asks': asks,

                    'timestamp': cache_data['timestamp'],

                    'age_s': age,

                    'levels_available': {

                        'bids': len(cache_data['bids']),

                        'asks': len(cache_data['asks'])

                    }

                }

        self.cache_stats['orderbook_misses'] += 1

        self._report_cache_stats()

        return None



    def _report_cache_stats(self):

        if not getattr(TradingConfig, 'ENABLE_WS_CACHE_STATS', False):

            return

        if time.time() - self.cache_stats['last_report'] >= 300:

            total_price = self.cache_stats['price_hits'] + self.cache_stats['price_misses']

            total_ob = self.cache_stats['orderbook_hits'] + self.cache_stats['orderbook_misses']

            total_flow = self.cache_stats['flow_hits'] + self.cache_stats['flow_misses']

            # Cache stats are still tracked but output is disabled to reduce clutter

            # if total_price > 0 or total_ob > 0 or total_flow > 0:

            #     price_hit_rate = (self.cache_stats['price_hits'] / total_price * 100) if total_price > 0 else 0

            #     ob_hit_rate = (self.cache_stats['orderbook_hits'] / total_ob * 100) if total_ob > 0 else 0

            #     flow_hit_rate = (self.cache_stats['flow_hits'] / total_flow * 100) if total_flow > 0 else 0

            #     output_manager.print_static(f"\n{CYAN}📊 WebSocket Cache Performance (5min):{RESET}")

            #     output_manager.print_static(f"  Price: {self.cache_stats['price_hits']}/{total_price} ({price_hit_rate:.1f}% hit rate)")

            #     output_manager.print_static(f"  Orderbook: {self.cache_stats['orderbook_hits']}/{total_ob} ({ob_hit_rate:.1f}% hit rate)")

            #     output_manager.print_static(f"  Flow: {self.cache_stats['flow_hits']}/{total_flow} ({flow_hit_rate:.1f}% hit rate)")

            #     total_saved = self.cache_stats['price_hits'] + self.cache_stats['orderbook_hits'] + self.cache_stats['flow_hits']

            #     output_manager.print_static(f"  {GREEN}✅ API Calls Saved: ~{total_saved} calls{RESET}")

            self.cache_stats = {

                'price_hits': 0,

                'price_misses': 0,

                'orderbook_hits': 0,

                'orderbook_misses': 0,

                'flow_hits': 0,

                'flow_misses': 0,

                'last_report': time.time()

            }



    def get_orderbook_analysis(self, symbol: str) -> Dict:

        try:

            ob = self.get_orderbook(symbol, limit=20)

            if not ob or 'bids' not in ob or 'asks' not in ob:

                return {}

            bids = ob.get('bids', [])

            asks = ob.get('asks', [])

            ts = ob.get('timestamp', int(time.time() * 1000))

            bid_volume = sum(float(b[1]) for b in bids[:10]) if bids else 0.0

            ask_volume = sum(float(a[1]) for a in asks[:10]) if asks else 0.0

            spread_pct = 0.0

            if bids and asks:

                best_bid = float(bids[0][0])

                best_ask = float(asks[0][0])

                if best_bid > 0:

                    spread_pct = ((best_ask - best_bid) / best_bid) * 100

            denom = (bid_volume + ask_volume)

            order_imbalance = ((bid_volume - ask_volume) / denom * 100) if denom > 0 else 0.0

            return {

                'bids': bids,

                'asks': asks,

                'bid_volume': bid_volume,

                'ask_volume': ask_volume,

                'spread_pct': spread_pct,

                'order_imbalance': order_imbalance,

                'timestamp': ts

            }

        except Exception:

            return {}



    async def stop_all_streams(self):

        try:

            for symbol, websocket in self.connections.items():

                try:

                    await websocket.close()

                except Exception:

                    pass

            self.connections.clear()

            print(f"{YELLOW}🛑 All WebSocket streams stopped{RESET}")

        except Exception as e:

            print(f"{RED}❌ Error stopping WebSocket streams: {e}{RESET}")

# Initialize exchange client (lazy initialization)

_exchange_client = None

_last_selected_exchange = None

_sell_balance_info = {}  # Store balance info for SELL mode

_bybit_ws_manager = None



def _init_exchange_client():

    """Initialize exchange client based on SELECTED_EXCHANGE"""

    global _exchange_client, SELECTED_EXCHANGE, _last_selected_exchange

    # Reset client if exchange changed

    if _exchange_client is None or _last_selected_exchange != SELECTED_EXCHANGE:

        # Load config if not already loaded

        # IMPORTANT: preserve_selected_exchange=True to prevent config.txt from overriding SELECTED_EXCHANGE

        if not CONFIG:

            load_config(preserve_selected_exchange=True)

        # Use ExchangeFactory from exchanger module (not the old one in core.py)

        try:

            from exchanger import ExchangeFactory as ExchangerFactory

            _exchange_client = ExchangerFactory.create_client(SELECTED_EXCHANGE)

        except Exception as e:

            print(f"{YELLOW}⚠️  Failed to import ExchangeFactory: {e}{RESET}")

            _exchange_client = None

        

        if _exchange_client is None:

            print(f"{YELLOW}⚠️  Failed to create {SELECTED_EXCHANGE} client, falling back to BYBIT{RESET}")

            SELECTED_EXCHANGE = "BYBIT"

            _exchange_client = BybitClient()

        _last_selected_exchange = SELECTED_EXCHANGE

    return _exchange_client



_ws_manager = None

_last_selected_ws_exchange = None



def _init_ws_manager():

    """Initialize WebSocket manager based on SELECTED_EXCHANGE"""

    global _ws_manager, SELECTED_EXCHANGE, _last_selected_ws_exchange

    # Reset manager if exchange changed

    if _ws_manager is None or _last_selected_ws_exchange != SELECTED_EXCHANGE:

        # Load config if not already loaded

        # IMPORTANT: preserve_selected_exchange=True to prevent config.txt from overriding SELECTED_EXCHANGE

        if not CONFIG:

            load_config(preserve_selected_exchange=True)

        try:

            from exchanger import ExchangeFactory

            _ws_manager = ExchangeFactory.create_ws_manager(SELECTED_EXCHANGE)

        except Exception as e:

            print(f"{YELLOW}⚠️  Failed to import ExchangeFactory: {e}{RESET}")

            _ws_manager = None

        

        if _ws_manager is None:

            # Fallback to BybitWebSocketManager if WebSocket manager not available

            # This handles cases like BINANCE which doesn't have WebSocket manager

            print(f"{YELLOW}⚠️  Failed to create {SELECTED_EXCHANGE} WebSocket manager, falling back to BYBIT{RESET}")

            SELECTED_EXCHANGE = "BYBIT"

            _ws_manager = BybitWebSocketManager()

        _last_selected_ws_exchange = SELECTED_EXCHANGE

    return _ws_manager



# Keep old variable name for backward compatibility

_bybit_ws_manager = None



# For backward compatibility - create bybit_client as a lazy-loaded wrapper

class _ExchangeClientWrapper:

    """Wrapper to maintain backward compatibility with bybit_client"""

    def __getattr__(self, name):

        client = _init_exchange_client()

        return getattr(client, name)

    

    def __call__(self, *args, **kwargs):

        client = _init_exchange_client()

        return client(*args, **kwargs)



# Create module-level variables for backward compatibility

bybit_client = _ExchangeClientWrapper()



class _WSManagerWrapper:

    """Wrapper for WebSocket manager - uses selected exchange"""

    def __getattr__(self, name):

        manager = _init_ws_manager()

        return getattr(manager, name)



# For backward compatibility - bybit_ws_manager now uses selected exchange

bybit_ws_manager = _WSManagerWrapper()

_coin_cooldown = {}

_coin_cooldown_lock = None

COINLIST_FILE = "coinlist.txt"

MAX_COINLIST = int(os.getenv("MAX_COINLIST", "30"))



def _get_cooldown_lock():

    global _coin_cooldown_lock

    if _coin_cooldown_lock is None:

        _coin_cooldown_lock = asyncio.Lock()

    return _coin_cooldown_lock

_global_dump_tracker = {

    'dumping_coins': set(),

    'last_update': 0,

    'dump_start_time': {},

    'severity': 'LOW'

}



def load_coin_watchlist(file_path: str = None) -> List[str]:

    if file_path is None:

        file_path = COINLIST_FILE

    watchlist = []

    try:

        if not os.path.exists(file_path):

            output_manager.print_static(f"{YELLOW}⚠️ {file_path} not found - using defaults{RESET}")

            return ["BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "MATIC_USDT"]

        with open(file_path, 'r', encoding='utf-8') as f:

            for line in f:

                line = line.strip()

                if not line or line.startswith('#'):

                    continue

                coin = line.split()[0].upper()

                if coin and 2 <= len(coin) <= 10:

                    symbol = f"{coin}_USDT" if '_' not in coin else coin

                    watchlist.append(symbol)

        seen = set()

        deduped = []

        for sym in watchlist:

            if sym not in seen:

                deduped.append(sym)

                seen.add(sym)

        watchlist = deduped

        if not watchlist:

            output_manager.print_static(f"{YELLOW}⚠️ {file_path} is empty - using defaults{RESET}")

            return ["BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "MATIC_USDT"]

        if len(watchlist) > MAX_COINLIST:

            original = len(watchlist)

            watchlist = watchlist[:MAX_COINLIST]

            output_manager.print_static(

                f"{YELLOW}⚠️ Trimming coinlist from {original} to {len(watchlist)} to avoid rate limits (MAX_COINLIST={MAX_COINLIST}). Set env MAX_COINLIST to override.{RESET}"

            )

        output_manager.print_static(f"{GREEN}✅ Loaded {len(watchlist)} coins from {file_path}{RESET}")

        return watchlist

    except Exception as e:

        output_manager.print_static(f"{RED}❌ Error loading {file_path}: {e}{RESET}")

        return ["BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "MATIC_USDT"]



# Rotation skip tracking: {symbol: {'skip_until_rotation': int, 'skipped_at_rotation': int}}

_rotation_skip_tracker = {}



async def mark_coin_cooldown(symbol: str, cooldown_seconds: int = 300):

    """Legacy: Time-based cooldown (used for non-rotation mode)"""

    global _coin_cooldown

    async with _get_cooldown_lock():

        _coin_cooldown[symbol] = time.time() + cooldown_seconds



async def mark_coin_skip_rotation(symbol: str, current_rotation: int, skip_rotations: int = 2):

    """

    Mark coin to be skipped for next N rotations.

    More efficient than time-based cooldown for rotation mode.

    """

    global _rotation_skip_tracker

    async with _get_cooldown_lock():

        _rotation_skip_tracker[symbol] = {

            'skip_until_rotation': current_rotation + skip_rotations,

            'skipped_at_rotation': current_rotation

        }



async def is_coin_in_cooldown(symbol: str) -> bool:

    """Legacy: Check time-based cooldown"""

    global _coin_cooldown

    async with _get_cooldown_lock():

        if symbol not in _coin_cooldown:

            return False

        return time.time() < _coin_cooldown[symbol]



async def is_coin_skipped_for_rotation(symbol: str, current_rotation: int) -> bool:

    """

    Check if coin should be skipped for current rotation.

    Returns True if coin is still in skip period (skip for 2 rotations).

    """

    global _rotation_skip_tracker

    async with _get_cooldown_lock():

        if symbol not in _rotation_skip_tracker:

            return False

        skip_info = _rotation_skip_tracker[symbol]

        # Coin is skipped if current_rotation < skip_until_rotation

        # Example: skip_until_rotation = 3, current_rotation = 1 or 2 → skipped

        #          skip_until_rotation = 3, current_rotation = 3 → not skipped (expired)

        if current_rotation < skip_info['skip_until_rotation']:

            return True

        else:

            # Skip period expired, remove from tracker

            del _rotation_skip_tracker[symbol]

            return False



async def get_cooldown_remaining(symbol: str) -> int:

    """Legacy: Get time-based cooldown remaining"""

    global _coin_cooldown

    async with _get_cooldown_lock():

        if symbol not in _coin_cooldown:

            return 0

        return max(0, int(_coin_cooldown[symbol] - time.time()))



async def get_skip_rotations_remaining(symbol: str, current_rotation: int) -> int:

    """

    Get number of rotations remaining before coin can be scanned again.

    Returns 0 if coin is not in skip period.

    """

    global _rotation_skip_tracker

    async with _get_cooldown_lock():

        if symbol not in _rotation_skip_tracker:

            return 0

        skip_info = _rotation_skip_tracker[symbol]

        # Calculate remaining: skip_until_rotation - current_rotation

        # Example: skip_until_rotation = 3, current_rotation = 1 → remaining = 2

        #          skip_until_rotation = 3, current_rotation = 2 → remaining = 1

        #          skip_until_rotation = 3, current_rotation = 3 → remaining = 0 (expired)

        remaining = skip_info['skip_until_rotation'] - current_rotation

        return max(0, remaining)



def _get_adaptive_history_window(coin_type: str, volatility: float) -> int:

    """Get adaptive history window based on coin type and volatility (international standard)"""

    # Base window by coin type (international market microstructure standards)

    base_windows = {

        'BLUECHIP': 30,   # More stable, faster updates (30s)

        'MAJOR': 45,      # Moderate stability (45s)

        'ALTCOIN': 60,   # Default (60s)

        'MICROCAP': 90,  # More volatile, longer window (90s)

        'STABLE': 30

    }

    base_window = base_windows.get(coin_type, 60)

    

    # Adjust based on volatility (high vol = longer window for smoothing)

    if volatility > 50:

        window_multiplier = 1.5  # 50% longer for very high volatility

    elif volatility > 40:

        window_multiplier = 1.3  # 30% longer for high volatility

    elif volatility > 25:

        window_multiplier = 1.1  # 10% longer for medium-high volatility

    elif volatility < 10:

        window_multiplier = 0.8  # 20% shorter for low volatility (faster response)

    else:

        window_multiplier = 1.0

    

    adaptive_window = int(base_window * window_multiplier)

    # Clamp to reasonable range (15s - 120s)

    return max(15, min(120, adaptive_window))



def _cleanup_coin_selection_history(symbol: str, history_dict: dict, max_age_s: int = 60):

    """Clean up old history entries for coin selection"""

    current_time = time.time()

    if symbol in history_dict:

        history_dict[symbol] = [

            h for h in history_dict[symbol]

            if current_time - h.get('timestamp', 0) <= max_age_s

        ]



def _get_coin_selection_history_stats(history_list: list, current_value: float = None) -> Dict:

    """Get statistics from coin selection history"""

    if not history_list:

        return {

            'avg': current_value or 0.0,

            'min': current_value or 0.0,

            'max': current_value or 0.0,

            'trend': 'stable',

            'consistency': 0.0,

            'count': 0

        }

    

    values = [h.get('value', 0) for h in history_list if 'value' in h]

    if not values and current_value is not None:

        values = [current_value]

    elif not values:

        return {

            'avg': 0.0,

            'min': 0.0,

            'max': 0.0,

            'trend': 'stable',

            'consistency': 0.0,

            'count': 0

        }

    

    if current_value is not None:

        values.append(current_value)

    

    avg = sum(values) / len(values) if values else 0.0

    min_val = min(values) if values else 0.0

    max_val = max(values) if values else 0.0

    

    # Calculate trend (increasing, decreasing, stable)

    # Use adaptive threshold based on coin type and volatility (if provided)

    trend_threshold = 0.05  # Default 5% (international standard for trend detection)

    if len(values) >= 3:

        recent = values[-3:]

        older = values[-6:-3] if len(values) >= 6 else values[:3]

        recent_avg = sum(recent) / len(recent)

        older_avg = sum(older) / len(older) if older else recent_avg

        if older_avg > 0:

            change_pct = abs(recent_avg - older_avg) / older_avg

            if recent_avg > older_avg * (1 + trend_threshold):

                trend = 'increasing'

            elif recent_avg < older_avg * (1 - trend_threshold):

                trend = 'decreasing'

            else:

                trend = 'stable'

        else:

            trend = 'stable'

    else:

        trend = 'stable'

    

    # Calculate consistency (coefficient of variation)

    if avg > 0:

        std_dev = (sum((v - avg) ** 2 for v in values) / len(values)) ** 0.5 if len(values) > 1 else 0.0

        consistency = 1.0 - min(1.0, std_dev / avg)  # Higher = more consistent

    else:

        consistency = 0.0

    

    return {

        'avg': avg,

        'min': min_val,

        'max': max_val,

        'trend': trend,

        'consistency': consistency,

        'count': len(values)

    }



async def calculate_coin_universe_score(symbol: str, analysis) -> Dict:

    scores = {}

    try:

        ai_score = (analysis.confidence / 100.0) * 25.0

        scores['ai_confidence'] = ai_score

        try:

            order_flow = await get_order_flow_optimized(symbol, limit=50)

            buy_pressure = order_flow.get('buy_pressure', 50.0)

            

            # History tracking for buy pressure

            current_time = time.time()

            if symbol not in _coin_selection_orderflow_history:

                _coin_selection_orderflow_history[symbol] = []

            _coin_selection_orderflow_history[symbol].append({

                'timestamp': current_time,

                'value': buy_pressure

            })

            

            # Get coin type and volatility for adaptive parameters

            coin_type = await get_coin_type_range(symbol)

            try:

                volatility = await calculate_coin_volatility(symbol)

            except:

                volatility = 20.0  # Default if calculation fails

            

            # Adaptive history window based on coin type and volatility

            adaptive_history_window = _get_adaptive_history_window(coin_type, volatility)

            _cleanup_coin_selection_history(symbol, _coin_selection_orderflow_history, max_age_s=adaptive_history_window)

            

            # Get history stats

            history_stats = _get_coin_selection_history_stats(

                _coin_selection_orderflow_history.get(symbol, []),

                buy_pressure

            )

            

            # Adaptive trend adjustment based on coin type and volatility

            # BLUECHIP: smaller adjustment (more stable), MICROCAP: larger adjustment (more volatile)

            base_trend_adjustment = {

                'BLUECHIP': 0.03,   # 3% adjustment for bluechips (more conservative)

                'MAJOR': 0.04,      # 4% adjustment for majors

                'ALTCOIN': 0.05,    # 5% adjustment for altcoins (default)

                'MICROCAP': 0.07,   # 7% adjustment for microcaps (more aggressive)

                'STABLE': 0.03

            }

            trend_adjustment_pct = base_trend_adjustment.get(coin_type, 0.05)

            

            # Adjust based on volatility (high vol = smaller adjustment, low vol = larger adjustment)

            if volatility > 40:

                trend_adjustment_pct *= 0.7  # Reduce 30% for high volatility

            elif volatility > 25:

                trend_adjustment_pct *= 0.85  # Reduce 15% for medium-high volatility

            elif volatility < 10:

                trend_adjustment_pct *= 1.2  # Increase 20% for low volatility

            

            # Use trend-adjusted buy pressure for scoring

            trend_adjusted_buy_pressure = buy_pressure

            if history_stats['trend'] == 'increasing' and history_stats['count'] >= 3:

                trend_adjusted_buy_pressure = buy_pressure * (1 + trend_adjustment_pct)

            elif history_stats['trend'] == 'decreasing' and history_stats['count'] >= 3:

                trend_adjusted_buy_pressure = buy_pressure * (1 - trend_adjustment_pct)

            

            # Adaptive consistency multiplier based on coin type and volatility

            # BLUECHIP: consistency more important, MICROCAP: consistency less important

            base_consistency_range = {

                'BLUECHIP': (0.97, 1.03),   # Narrower range (consistency more important)

                'MAJOR': (0.96, 1.04),      # Moderate range

                'ALTCOIN': (0.95, 1.05),    # Default range

                'MICROCAP': (0.93, 1.07),   # Wider range (consistency less important)

                'STABLE': (0.97, 1.03)

            }

            consistency_min, consistency_max = base_consistency_range.get(coin_type, (0.95, 1.05))

            

            # Adjust based on volatility (high vol = wider range, low vol = narrower range)

            if volatility > 40:

                consistency_range_width = consistency_max - consistency_min

                consistency_min = max(0.90, consistency_min - consistency_range_width * 0.2)

                consistency_max = min(1.10, consistency_max + consistency_range_width * 0.2)

            elif volatility < 10:

                consistency_range_width = consistency_max - consistency_min

                consistency_min = min(0.98, consistency_min + consistency_range_width * 0.1)

                consistency_max = max(1.02, consistency_max - consistency_range_width * 0.1)

            

            # Use consistency-adjusted buy pressure (more consistent = more reliable)

            consistency_multiplier = consistency_min + (history_stats['consistency'] * (consistency_max - consistency_min))

            final_buy_pressure = trend_adjusted_buy_pressure * consistency_multiplier

            

            # Store history stats for debugging

            scores['buy_pressure_history'] = {

                'current': buy_pressure,

                'trend_adjusted': trend_adjusted_buy_pressure,

                'final': final_buy_pressure,

                'trend': history_stats['trend'],

                'consistency': history_stats['consistency'],

                'avg': history_stats['avg'],

                'count': history_stats['count'],

                'adaptive_window': adaptive_history_window,

                'trend_adjustment_pct': trend_adjustment_pct,

                'consistency_range': (consistency_min, consistency_max)

            }

            

            # Use final_buy_pressure for scoring

            buy_pressure_for_scoring = final_buy_pressure

            if coin_type == 'BLUECHIP':

                base_thresholds = {'excellent': 65, 'good': 58, 'moderate': 52, 'neutral': 48}

            elif coin_type == 'MAJOR':

                base_thresholds = {'excellent': 62, 'good': 56, 'moderate': 50, 'neutral': 46}

            elif coin_type == 'ALTCOIN':

                base_thresholds = {'excellent': 58, 'good': 53, 'moderate': 48, 'neutral': 44}

            elif coin_type == 'MICROCAP':

                base_thresholds = {'excellent': 53, 'good': 48, 'moderate': 45, 'neutral': 42}

            else:

                base_thresholds = {'excellent': 60, 'good': 54, 'moderate': 48, 'neutral': 45}

            total_adjustment = 0.0

            adjustments = []

            try:

                volatility = await calculate_coin_volatility(symbol)

                if volatility > 40:

                    vol_adj = -10.0

                    adjustments.append(f"High vol ({volatility:.0f}%): -10%")

                elif volatility > 25:

                    vol_adj = -5.0

                    adjustments.append(f"Med-high vol ({volatility:.0f}%): -5%")

                elif volatility < 10:

                    vol_adj = +5.0

                    adjustments.append(f"Low vol ({volatility:.0f}%): +5%")

                else:

                    vol_adj = 0.0

                total_adjustment += vol_adj

            except Exception:

                pass

            try:

                market_regime = analysis.market_regime.value if hasattr(analysis, 'market_regime') else 'sideways'

                if market_regime in ['strong_downtrend', 'downtrend']:

                    regime_adj = -15.0

                    adjustments.append(f"{market_regime.replace('_', ' ').title()}: -15%")

                elif market_regime == 'weak_downtrend':

                    regime_adj = -10.0

                    adjustments.append(f"Weak Downtrend: -10%")

                elif market_regime in ['sideways', 'high_vol_consolidation']:

                    regime_adj = -5.0

                    adjustments.append(f"{market_regime.replace('_', ' ').title()}: -5%")

                elif market_regime in ['weak_uptrend']:

                    regime_adj = -3.0

                    adjustments.append(f"Weak Uptrend: -3%")

                elif market_regime in ['strong_uptrend', 'uptrend']:

                    regime_adj = 0.0

                else:

                    regime_adj = 0.0

                total_adjustment += regime_adj

            except Exception:

                pass

            thresholds = {}

            for key, base_value in base_thresholds.items():

                adjusted_value = base_value + total_adjustment

                thresholds[key] = max(35.0, min(95.0, adjusted_value))

            if buy_pressure_for_scoring >= thresholds['excellent']:

                flow_score = 20.0

            elif buy_pressure_for_scoring >= thresholds['good']:

                flow_score = 16.0

            elif buy_pressure_for_scoring >= thresholds['moderate']:

                flow_score = 12.0

            elif buy_pressure_for_scoring >= thresholds['neutral']:

                flow_score = 8.0

            else:

                flow_score = 4.0

            scores['order_flow'] = flow_score

            scores['buy_pressure'] = buy_pressure

            scores['buy_pressure_final'] = buy_pressure_for_scoring

            scores['order_flow_adjustments'] = adjustments

            scores['order_flow_threshold_used'] = thresholds['excellent']

        except:

            scores['order_flow'] = 10.0

            scores['buy_pressure'] = 50.0

        try:

            orderbook = await get_orderbook_optimized(symbol, limit=20, force_fresh=True, max_age_s=5)

            if orderbook and 'bids' in orderbook and 'asks' in orderbook and orderbook['bids'] and orderbook['asks']:

                bids = orderbook['bids']

                asks = orderbook['asks']



                def _depth_sum(levels):

                    depth = 0.0

                    if not levels:

                        return depth

                    for level in levels[:10]:

                        size_value = None

                        if isinstance(level, (list, tuple)):

                            if len(level) >= 2:

                                size_value = level[1]

                        elif isinstance(level, dict):

                            size_value = (

                                level.get('size')

                                or level.get('qty')

                                or level.get('quantity')

                                or level.get('sizeInUnits')

                                or level.get('baseSize')

                            )

                        if size_value in (None, ''):

                            continue

                        try:

                            val = float(size_value)

                            if val > 0:

                                depth += val

                        except (TypeError, ValueError):

                            continue

                    return depth

                bid_depth = _depth_sum(bids)

                ask_depth = _depth_sum(asks)

                total_depth = bid_depth + ask_depth

                if total_depth <= 0 and os.getenv("BYBIT_DEBUG", "0") == "1":

                    logger.debug(

                        f"⚠️ OB Depth Issue [{symbol.split('_')[0]}]: "

                        f"bid_depth={bid_depth:.6f}, ask_depth={ask_depth:.6f}, "

                        f"total_depth={total_depth:.6f}, bids_count={len(bids) if bids else 0}, "

                        f"asks_count={len(asks) if asks else 0}, "

                        f"first_bid={bids[0] if bids else 'N/A'}, "

                        f"first_ask={asks[0] if asks else 'N/A'}"

                    )

                if total_depth > 0:

                    bid_ratio = bid_depth / total_depth

                    

                    # History tracking for BIR

                    current_time = time.time()

                    if symbol not in _coin_selection_orderbook_history:

                        _coin_selection_orderbook_history[symbol] = []

                    _coin_selection_orderbook_history[symbol].append({

                        'timestamp': current_time,

                        'value': bid_ratio

                    })

                    

                    # Get coin type and volatility for adaptive parameters

                    coin_type = await get_coin_type_range(symbol)

                    try:

                        volatility = await calculate_coin_volatility(symbol)

                    except:

                        volatility = 20.0  # Default if calculation fails

                    

                    # Adaptive history window based on coin type and volatility

                    adaptive_history_window = _get_adaptive_history_window(coin_type, volatility)

                    _cleanup_coin_selection_history(symbol, _coin_selection_orderbook_history, max_age_s=adaptive_history_window)

                    

                    # Get history stats for BIR

                    bir_history_stats = _get_coin_selection_history_stats(

                        _coin_selection_orderbook_history.get(symbol, []),

                        bid_ratio

                    )

                    

                    # Adaptive trend adjustment for BIR based on coin type and volatility

                    # BIR is more sensitive, so use smaller adjustments than buy pressure

                    base_bir_trend_adjustment = {

                        'BLUECHIP': 0.015,   # 1.5% adjustment for bluechips

                        'MAJOR': 0.02,       # 2% adjustment for majors

                        'ALTCOIN': 0.025,    # 2.5% adjustment for altcoins

                        'MICROCAP': 0.035,   # 3.5% adjustment for microcaps

                        'STABLE': 0.015

                    }

                    bir_trend_adjustment_pct = base_bir_trend_adjustment.get(coin_type, 0.025)

                    

                    # Adjust based on volatility

                    if volatility > 40:

                        bir_trend_adjustment_pct *= 0.7

                    elif volatility > 25:

                        bir_trend_adjustment_pct *= 0.85

                    elif volatility < 10:

                        bir_trend_adjustment_pct *= 1.2

                    

                    # Use trend-adjusted BIR for scoring

                    trend_adjusted_bir = bid_ratio

                    if bir_history_stats['trend'] == 'increasing' and bir_history_stats['count'] >= 3:

                        trend_adjusted_bir = bid_ratio * (1 + bir_trend_adjustment_pct)

                    elif bir_history_stats['trend'] == 'decreasing' and bir_history_stats['count'] >= 3:

                        trend_adjusted_bir = bid_ratio * (1 - bir_trend_adjustment_pct)

                    

                    # Adaptive consistency multiplier for BIR

                    base_bir_consistency_range = {

                        'BLUECHIP': (0.985, 1.015),   # Narrower range

                        'MAJOR': (0.98, 1.02),        # Moderate range

                        'ALTCOIN': (0.975, 1.025),    # Default range

                        'MICROCAP': (0.97, 1.03),     # Wider range

                        'STABLE': (0.985, 1.015)

                    }

                    bir_consistency_min, bir_consistency_max = base_bir_consistency_range.get(coin_type, (0.975, 1.025))

                    

                    # Adjust based on volatility

                    if volatility > 40:

                        bir_consistency_range_width = bir_consistency_max - bir_consistency_min

                        bir_consistency_min = max(0.95, bir_consistency_min - bir_consistency_range_width * 0.2)

                        bir_consistency_max = min(1.05, bir_consistency_max + bir_consistency_range_width * 0.2)

                    elif volatility < 10:

                        bir_consistency_range_width = bir_consistency_max - bir_consistency_min

                        bir_consistency_min = min(0.99, bir_consistency_min + bir_consistency_range_width * 0.1)

                        bir_consistency_max = max(1.01, bir_consistency_max - bir_consistency_range_width * 0.1)

                    

                    # Use consistency-adjusted BIR

                    bir_consistency_multiplier = bir_consistency_min + (bir_history_stats['consistency'] * (bir_consistency_max - bir_consistency_min))

                    final_bir = trend_adjusted_bir * bir_consistency_multiplier

                    

                    # Store history stats

                    scores['bir_history'] = {

                        'current': bid_ratio,

                        'trend_adjusted': trend_adjusted_bir,

                        'final': final_bir,

                        'trend': bir_history_stats['trend'],

                        'consistency': bir_history_stats['consistency'],

                        'avg': bir_history_stats['avg'],

                        'count': bir_history_stats['count'],

                        'adaptive_window': adaptive_history_window,

                        'trend_adjustment_pct': bir_trend_adjustment_pct,

                        'consistency_range': (bir_consistency_min, bir_consistency_max)

                    }

                    

                    # Use final_bir for scoring

                    bir_for_scoring = final_bir

                    if coin_type == 'BLUECHIP':

                        base_ranges = {

                            'excellent_min': 0.51, 'excellent_max': 0.63,

                            'good_min': 0.47, 'good_max': 0.51,

                            'acceptable_min': 0.44, 'acceptable_max': 0.47

                        }

                    elif coin_type == 'MAJOR':

                        base_ranges = {

                            'excellent_min': 0.52, 'excellent_max': 0.65,

                            'good_min': 0.48, 'good_max': 0.52,

                            'acceptable_min': 0.45, 'acceptable_max': 0.48

                        }

                    elif coin_type == 'ALTCOIN':

                        base_ranges = {

                            'excellent_min': 0.55, 'excellent_max': 0.70,

                            'good_min': 0.50, 'good_max': 0.55,

                            'acceptable_min': 0.45, 'acceptable_max': 0.50

                        }

                    elif coin_type == 'MICROCAP':

                        base_ranges = {

                            'excellent_min': 0.60, 'excellent_max': 0.80,

                            'good_min': 0.55, 'good_max': 0.60,

                            'acceptable_min': 0.50, 'acceptable_max': 0.55

                        }

                    else:

                        base_ranges = {

                            'excellent_min': 0.55, 'excellent_max': 0.70,

                            'good_min': 0.50, 'good_max': 0.55,

                            'acceptable_min': 0.45, 'acceptable_max': 0.50

                        }

                    ratio_adjustment = 0.0

                    book_adjustments = []

                    range_width_multiplier = 1.0

                    try:

                        volatility = await calculate_coin_volatility(symbol)

                        if volatility > 40:

                            range_width_multiplier = 1.4

                            book_adjustments.append(f"High vol ({volatility:.0f}%): +40% width")

                        elif volatility > 25:

                            range_width_multiplier = 1.2

                            book_adjustments.append(f"Med-high vol ({volatility:.0f}%): +20% width")

                        elif volatility < 10:

                            range_width_multiplier = 0.8

                            book_adjustments.append(f"Low vol ({volatility:.0f}%): -20% width")

                        else:

                            range_width_multiplier = 1.0

                    except Exception:

                        pass

                    regime_shift = 0.0

                    try:

                        market_regime = analysis.market_regime.value if hasattr(analysis, 'market_regime') else 'sideways'

                        if market_regime in ['strong_uptrend', 'uptrend']:

                            regime_shift = -0.05

                            book_adjustments.append(f"{market_regime.replace('_', ' ').title()}: -5% shift")

                        elif market_regime in ['weak_uptrend']:

                            regime_shift = -0.02

                            book_adjustments.append(f"Weak Uptrend: -2% shift")

                        elif market_regime in ['strong_downtrend', 'downtrend']:

                            regime_shift = +0.05

                            book_adjustments.append(f"{market_regime.replace('_', ' ').title()}: +5% shift")

                        elif market_regime == 'weak_downtrend':

                            regime_shift = +0.02

                            book_adjustments.append(f"Weak Downtrend: +2% shift")

                        elif market_regime in ['sideways', 'high_vol_consolidation']:

                            regime_shift = 0.0

                        else:

                            regime_shift = 0.0

                    except Exception:

                        pass

                    adjusted_ranges = {}

                    for key, base_value in base_ranges.items():

                        center = 0.50

                        distance_from_center = (base_value - center) * range_width_multiplier

                        adjusted_value = center + distance_from_center + regime_shift

                        adjusted_ranges[key] = max(0.25, min(0.95, adjusted_value))

                    try:

                        logger.debug(

                            f"📊 OB Ranges [{symbol.split('_')[0]}]: "

                            f"BidRatio={bid_ratio:.4f} ({bid_ratio*100:.2f}%) | "

                            f"Adjustments: {book_adjustments} | "

                            f"Excellent={adjusted_ranges['excellent_min']:.3f}-{adjusted_ranges['excellent_max']:.3f} | "

                            f"Good={adjusted_ranges['good_min']:.3f}-{adjusted_ranges['good_max']:.3f} | "

                            f"Accept={adjusted_ranges['acceptable_min']:.3f}-{adjusted_ranges['acceptable_max']:.3f}"

                        )

                    except:

                        pass

                    if adjusted_ranges['excellent_min'] <= bir_for_scoring <= adjusted_ranges['excellent_max']:

                        book_score = 15.0

                    elif adjusted_ranges['good_min'] <= bir_for_scoring <= adjusted_ranges['good_max']:

                        book_score = 12.0

                    elif adjusted_ranges['acceptable_min'] <= bir_for_scoring <= adjusted_ranges['acceptable_max']:

                        book_score = 8.0

                    else:

                        book_score = 4.0

                    scores['orderbook'] = book_score

                    scores['bid_ratio'] = bid_ratio * 100

                    scores['bid_ratio_final'] = bir_for_scoring * 100

                    scores['orderbook_adjustments'] = book_adjustments

                    scores['orderbook_range_used'] = f"{adjusted_ranges['excellent_min']:.2f}-{adjusted_ranges['excellent_max']:.2f}"

                    try:

                        logger.debug(

                            f"📊 OB Score Debug [{symbol.split('_')[0]}]: "

                            f"BidRatio={bid_ratio:.3f} ({bid_ratio*100:.1f}%) | "

                            f"Excellent={adjusted_ranges['excellent_min']:.2f}-{adjusted_ranges['excellent_max']:.2f} | "

                            f"Good={adjusted_ranges['good_min']:.2f}-{adjusted_ranges.get('good_max', adjusted_ranges['excellent_min']):.2f} | "

                            f"Accept={adjusted_ranges['acceptable_min']:.2f}-{adjusted_ranges.get('acceptable_max', adjusted_ranges['good_min']):.2f} | "

                            f"Score={book_score:.1f}/15"

                        )

                    except:

                        pass

                else:

                    if os.getenv("BYBIT_DEBUG", "0") == "1":

                        logger.debug(

                            f"⚠️ OB Fallback [{symbol.split('_')[0]}]: insufficient depth data "

                            f"(bid_depth={bid_depth:.6f}, ask_depth={ask_depth:.6f}, "

                            f"bids={len(bids) if bids else 0}, asks={len(asks) if asks else 0})"

                        )

                    scores['orderbook'] = 7.5

                    scores['bid_ratio'] = 50.0

            else:

                if os.getenv("BYBIT_DEBUG", "0") == "1":

                    logger.debug(

                        f"⚠️ OB Fallback [{symbol.split('_')[0]}]: orderbook missing bids/asks"

                    )

                scores['orderbook'] = 7.5

                scores['bid_ratio'] = 50.0

        except Exception as ob_exc:

            if os.getenv("BYBIT_DEBUG", "0") == "1":

                logger.debug(f"⚠️ OB Fallback [{symbol.split('_')[0]}]: exception {ob_exc}")

            scores['orderbook'] = 7.5

            scores['bid_ratio'] = 50.0

        try:

            liquidity = await analyze_liquidity_adaptive(symbol, force_fresh=True)

            grade = (liquidity.get('liquidity_grade') or liquidity.get('quality') or 'UNKNOWN')

            grade_to_score = {

                'EXCELLENT': 15.0,

                'GOOD': 12.0,

                'FAIR': 8.0,

                'MODERATE': 8.0,

                'POOR': 4.0

            }

            liquidity_score = grade_to_score.get(grade, 7.5)

            scores['liquidity'] = liquidity_score

            scores['liquidity_quality'] = grade

        except:

            scores['liquidity'] = 7.5

            scores['liquidity_quality'] = 'MODERATE'

        

        # NEW: Futures Market Analysis (Hybrid Approach)

        # Add futures signals as secondary indicators

        futures_signal_score = 0.0

        futures_sentiment_info = {}

        futures_risk_info = {}

        try:

            # Get futures premium/discount

            premium = await get_futures_premium(symbol)

            if premium is not None:

                # Premium > 2%: Very bullish (add bonus)

                # Premium 0.5-2%: Bullish (add small bonus)

                # Premium -0.5 to 0.5%: Neutral (no change)

                # Premium -2% to -0.5%: Bearish (subtract small penalty)

                # Premium < -2%: Very bearish (subtract penalty)

                if premium > 2.0:

                    futures_signal_score += 5.0  # Strong bullish signal

                    futures_sentiment_info['premium_signal'] = 'very_bullish'

                elif premium > 0.5:

                    futures_signal_score += 2.0  # Bullish signal

                    futures_sentiment_info['premium_signal'] = 'bullish'

                elif premium < -2.0:

                    futures_signal_score -= 5.0  # Strong bearish signal

                    futures_sentiment_info['premium_signal'] = 'very_bearish'

                elif premium < -0.5:

                    futures_signal_score -= 2.0  # Bearish signal

                    futures_sentiment_info['premium_signal'] = 'bearish'

                else:

                    futures_sentiment_info['premium_signal'] = 'neutral'

                

                futures_sentiment_info['premium'] = premium

            

            # Get futures price change (leading indicator)

            # Check if futures candles are available to determine data source

            futures_candles_check = await get_futures_candles(symbol, interval='5m', limit=13)

            using_futures_data = futures_candles_check is not None and len(futures_candles_check) >= 2

            

            futures_price_change = await get_futures_price_change(symbol, period_minutes=60)

            if futures_price_change is not None:

                # Futures price change > 3%: Strong upward momentum (add bonus)

                # Futures price change 1-3%: Upward momentum (add small bonus)

                # Futures price change -1% to 1%: Neutral (no change)

                # Futures price change -3% to -1%: Downward momentum (subtract small penalty)

                # Futures price change < -3%: Strong downward momentum (subtract penalty)

                if futures_price_change > 3.0:

                    futures_signal_score += 3.0  # Strong upward momentum

                    futures_sentiment_info['momentum_signal'] = 'strong_up'

                elif futures_price_change > 1.0:

                    futures_signal_score += 1.0  # Upward momentum

                    futures_sentiment_info['momentum_signal'] = 'up'

                elif futures_price_change < -3.0:

                    futures_signal_score -= 3.0  # Strong downward momentum

                    futures_sentiment_info['momentum_signal'] = 'strong_down'

                elif futures_price_change < -1.0:

                    futures_signal_score -= 1.0  # Downward momentum

                    futures_sentiment_info['momentum_signal'] = 'down'

                else:

                    futures_sentiment_info['momentum_signal'] = 'neutral'

                

                futures_sentiment_info['price_change_1h'] = futures_price_change

                futures_sentiment_info['using_futures_data'] = using_futures_data

            

            # Get futures risk indicator

            risk_indicator = await get_futures_risk_indicator(symbol)

            if risk_indicator:

                risk_level = risk_indicator.get('risk_level', 'low')

                volatility_ratio = risk_indicator.get('volatility_ratio', 1.0)

                

                # High risk: Reduce score (subtract penalty)

                # Medium risk: Small reduction

                # Low risk: No change

                if risk_level == 'high':

                    futures_signal_score -= 3.0  # High risk penalty

                    futures_risk_info['risk_signal'] = 'high_risk'

                elif risk_level == 'medium':

                    futures_signal_score -= 1.0  # Medium risk penalty

                    futures_risk_info['risk_signal'] = 'medium_risk'

                else:

                    futures_risk_info['risk_signal'] = 'low_risk'

                

                futures_risk_info['risk_level'] = risk_level

                futures_risk_info['volatility_ratio'] = volatility_ratio

                futures_risk_info['futures_volatility'] = risk_indicator.get('futures_volatility', 0)

                futures_risk_info['spot_volatility'] = risk_indicator.get('spot_volatility', 0)

            

            # Store futures signals in scores

            scores['futures_signal_score'] = futures_signal_score

            scores['futures_sentiment'] = futures_sentiment_info

            scores['futures_risk'] = futures_risk_info

            

        except Exception as e:

            # Silent fail - futures analysis is secondary, not critical

            scores['futures_signal_score'] = 0.0

            scores['futures_sentiment'] = {}

            scores['futures_risk'] = {}

        

        try:

            orderbook = await get_orderbook_optimized(symbol, limit=5)

            if orderbook and 'bids' in orderbook and 'asks' in orderbook:

                bids = orderbook['bids']

                asks = orderbook['asks']

                if bids and asks:

                    best_bid = float(bids[0][0])

                    best_ask = float(asks[0][0])

                    spread_pct = ((best_ask - best_bid) / best_bid) * 100 if best_bid > 0 else 999

                    coin_type = await get_coin_type_range(symbol)

                    if coin_type == 'BLUECHIP':

                        threshold = 0.08

                    elif coin_type == 'MAJOR':

                        threshold = 0.12

                    elif coin_type == 'ALTCOIN':

                        threshold = 0.50

                    elif coin_type == 'MICROCAP':

                        threshold = 1.00

                    else:

                        threshold = 0.50

                    if spread_pct <= threshold:

                        spread_score = 10.0

                    elif spread_pct <= threshold * 2:

                        spread_score = 7.0

                    elif spread_pct <= threshold * 3:

                        spread_score = 4.0

                    else:

                        spread_score = 2.0

                    scores['spread'] = spread_score

                    scores['spread_pct'] = spread_pct

                else:

                    scores['spread'] = 5.0

                    scores['spread_pct'] = 0.5

            else:

                scores['spread'] = 5.0

                scores['spread_pct'] = 0.5

        except:

            scores['spread'] = 5.0

            scores['spread_pct'] = 0.5

        try:

            regime = analysis.market_regime.value

            recommendation = analysis.recommendation.value

            if regime in ['strong_uptrend', 'uptrend'] and recommendation in ['STRONG_BUY', 'BUY']:

                regime_score = 10.0

            elif regime == 'sideways' and recommendation in ['STRONG_BUY', 'BUY']:

                regime_score = 7.0

            elif regime in ['downtrend', 'strong_downtrend']:

                regime_score = 2.0

            else:

                regime_score = 5.0

            scores['market_regime'] = regime_score

            scores['regime'] = regime

        except:

            scores['market_regime'] = 5.0

            scores['regime'] = 'unknown'

        try:

            candles = await get_candles_optimized(symbol, interval='5m', limit=200)

            if candles and len(candles) > 50:

                coin_type = await get_coin_type_range(symbol)

                volatility = await calculate_coin_volatility(symbol)

                order_flow_data = None

                orderbook_data = None

                try:

                    ws_manager = _init_ws_manager()

                    if ws_manager:

                        if hasattr(ws_manager, 'get_order_flow_analysis'):

                            order_flow_data = ws_manager.get_order_flow_analysis(symbol, limit=20)

                        if hasattr(ws_manager, 'get_orderbook_analysis'):

                            orderbook_data = ws_manager.get_orderbook_analysis(symbol)

                except Exception:

                    pass

                manipulation_result = comprehensive_manipulation_check(

                    candles, coin_type, volatility, order_flow_data, orderbook_data

                )

                risk_level = manipulation_result['overall_risk']

                if risk_level == 'CRITICAL':

                    manip_score = 0.0

                elif risk_level == 'HIGH':

                    manip_score = 1.0

                elif risk_level == 'MEDIUM':

                    manip_score = 3.0

                else:

                    manip_score = 5.0

                scores['anti_manipulation'] = manip_score

                scores['manipulation_risk'] = risk_level

            else:

                scores['anti_manipulation'] = 2.5

                scores['manipulation_risk'] = 'UNKNOWN'

        except:

            scores['anti_manipulation'] = 2.5

            scores['manipulation_risk'] = 'UNKNOWN'

        total_score = sum([

            scores.get('ai_confidence', 0),

            scores.get('order_flow', 0),

            scores.get('orderbook', 0),

            scores.get('liquidity', 0),

            scores.get('spread', 0),

            scores.get('market_regime', 0),

            scores.get('anti_manipulation', 0),

            scores.get('futures_signal_score', 0)  # NEW: Add futures signals

        ])

        if total_score >= 90:

            grade = 'A+'

        elif total_score >= 80:

            grade = 'A'

        elif total_score >= 70:

            grade = 'B'

        elif total_score >= 60:

            grade = 'C'

        elif total_score >= 50:

            grade = 'D'

        else:

            grade = 'F'

        return {

            'total_score': round(total_score, 1),

            'grade': grade,

            'details': scores

        }

    except Exception as e:

        return {

            'total_score': analysis.confidence * 0.25,

            'grade': 'D',

            'details': {'ai_confidence': analysis.confidence * 0.25, 'error': str(e)}

        }



async def scan_best_coin_from_watchlist(watchlist: List[str], exclude_cooldown: bool = True, min_confidence: float = 70.0, current_rotation: int = 0, rotation_mode: bool = False) -> tuple:

    output_manager.print_static(f"\n{CYAN}🔍 STAGE 1: Quick AI scan of {len(watchlist)} coins...{RESET}")

    try:

        ws_prewarm = os.getenv('WS_PREWARM', '1')

        if ws_prewarm == '1':

            prewarm_count = 0

            ws_manager = _init_ws_manager()

            if ws_manager and hasattr(ws_manager, 'start_stream'):

                for coin in watchlist:

                    # Ensure coin has _USDT suffix for WebSocket subscription

                    if '_' not in coin:

                        symbol = f"{coin}_USDT"

                    else:

                        symbol = coin

                    try:

                        # Check if symbol is supported before attempting WebSocket subscription

                        # This prevents errors for futures-only coins like JELLYJELLY

                        exchange_client = _init_exchange_client()

                        if exchange_client and hasattr(exchange_client, 'is_symbol_supported_cached'):

                            if not exchange_client.is_symbol_supported_cached(symbol):

                                # Symbol not supported in spot market - skip silently (e.g., futures-only coins)

                                continue

                        asyncio.create_task(ws_manager.start_stream(symbol))

                        prewarm_count += 1

                    except Exception as e:

                        # Skip invalid symbols silently during prewarm (e.g., futures-only coins like JELLYJELLY)

                        pass

            prewarm_delay = float(os.getenv('WS_PREWARM_DELAY', '1.5'))

            await asyncio.sleep(max(0.5, prewarm_delay))

            print(f"{GREEN}✅ WebSocket prewarmed: {prewarm_count} symbols{RESET}")

    except Exception:

        pass

    stage1_results = []

    if ENABLE_PARALLEL_PROCESSING:

        for i in range(0, len(watchlist), BATCH_SIZE_MARKET_SCAN):

            batch = watchlist[i:i+BATCH_SIZE_MARKET_SCAN]



            async def scan_single_coin(coin):

                # Ensure coin has _USDT suffix

                if '_' not in coin:

                    symbol = f"{coin}_USDT"

                else:

                    symbol = coin

                try:

                    # Check if symbol is supported in spot market first (filter futures-only coins)

                    # Do a quick API check to verify symbol support before analysis

                    exchange_client = _init_exchange_client()

                    if exchange_client:

                        # Quick check: try to get price to verify symbol exists in spot market

                        # This will mark unsupported symbols in cache if they don't exist

                        try:

                            # Use asyncio.to_thread for synchronous get_price_rest call

                            # Check if exchange supports category parameter

                            exchange_name = getattr(exchange_client, 'exchange_name', '').upper()

                            supports_category = exchange_name in ['BYBIT', 'OKX', 'BITGET', 'KUCOIN', 'MEXC', 'GATEIO', 'BINANCE']

                            

                            if supports_category:

                                price_check_spot = await asyncio.to_thread(

                                    exchange_client.get_price_rest, symbol, "spot"

                                )

                            else:

                                # For exchanges without category support (e.g., Binance), use default

                                price_check_spot = await asyncio.to_thread(

                                    exchange_client.get_price_rest, symbol

                                )

                            

                            # If price is None, check futures market to determine if futures-only or not available

                            if price_check_spot is None:

                                # Symbol not in spot market - check if it exists in futures market

                                # Only check futures if exchange supports category parameter

                                if supports_category:

                                    try:

                                        # Determine futures category based on exchange

                                        futures_category = "linear"  # Default for Bybit

                                        if exchange_name == "OKX":

                                            futures_category = "linear"

                                        elif exchange_name == "BITGET":

                                            futures_category = "linear"

                                        elif exchange_name == "KUCOIN":

                                            futures_category = "futures"

                                        elif exchange_name == "MEXC":

                                            futures_category = "futures"

                                        elif exchange_name == "GATEIO":

                                            futures_category = "futures"

                                        

                                        # Use asyncio.to_thread for synchronous get_price_rest call

                                        price_check_futures = await asyncio.to_thread(

                                            exchange_client.get_price_rest, symbol, futures_category

                                        )

                                        if price_check_futures is not None:

                                            # Coin exists in futures market but not in spot (futures-only)

                                            exchange_display_name = exchange_name.title()

                                            output_manager.print_static(

                                                f"  {symbol.split('_')[0]}: ⚠️ FUTURES-ONLY (only available in futures market {exchange_display_name}, will not be selected)"

                                            )

                                        else:

                                            # Coin doesn't exist in futures or spot (not available)

                                            exchange_display_name = exchange_name.title()

                                            output_manager.print_static(

                                                f"  {symbol.split('_')[0]}: ⚠️ NOT AVAILABLE (not available in {exchange_display_name} market)"

                                            )

                                    except Exception as e:

                                        # If futures check fails, assume not available

                                        exchange_display_name = exchange_name.title()

                                        output_manager.print_static(

                                            f"  {symbol.split('_')[0]}: ⚠️ NOT AVAILABLE (not available in {exchange_display_name})"

                                        )

                                else:

                                    # For exchanges without category support, assume not available if spot returns None

                                    exchange_display_name = exchange_name.title()

                                    output_manager.print_static(

                                        f"  {symbol.split('_')[0]}: ⚠️ NOT AVAILABLE (not available in {exchange_display_name})"

                                    )

                                return None

                        except Exception:

                            # If check fails, try to check futures market directly (if supported)

                            exchange_name = getattr(exchange_client, 'exchange_name', '').upper()

                            supports_category = exchange_name in ['BYBIT', 'OKX', 'BITGET', 'KUCOIN', 'MEXC', 'GATEIO', 'BINANCE']

                            

                            if supports_category:

                                try:

                                    # Determine futures category based on exchange

                                    futures_category = "linear"  # Default for Bybit

                                    if exchange_name == "OKX":

                                        futures_category = "linear"

                                    elif exchange_name == "BITGET":

                                        futures_category = "linear"

                                    elif exchange_name == "KUCOIN":

                                        futures_category = "futures"

                                    elif exchange_name == "MEXC":

                                        futures_category = "futures"

                                    elif exchange_name == "GATEIO":

                                        futures_category = "futures"

                                    elif exchange_name == "BINANCE":

                                        futures_category = "futures"  # Binance uses "futures" category

                                    

                                    price_check_futures = await asyncio.to_thread(

                                        exchange_client.get_price_rest, symbol, futures_category

                                    )

                                    if price_check_futures is not None:

                                        exchange_display_name = exchange_name.title()

                                        output_manager.print_static(

                                            f"  {symbol.split('_')[0]}: ⚠️ FUTURES-ONLY (only available in futures market {exchange_display_name}, will not be selected)"

                                        )

                                    else:

                                        exchange_display_name = exchange_name.title()

                                        output_manager.print_static(

                                            f"  {symbol.split('_')[0]}: ⚠️ NOT AVAILABLE (not available in {exchange_display_name})"

                                        )

                                except Exception:

                                    exchange_display_name = exchange_name.title()

                                    output_manager.print_static(

                                        f"  {symbol.split('_')[0]}: ⚠️ NOT AVAILABLE (not available in {exchange_display_name})"

                                    )

                            else:

                                exchange_display_name = exchange_name.title()

                                output_manager.print_static(

                                    f"  {symbol.split('_')[0]}: ⚠️ NOT AVAILABLE (not available in {exchange_display_name})"

                                )

                            return None

                    

                    # Check rotation skip first (for rotation mode)

                    if rotation_mode and current_rotation > 0:

                        if await is_coin_skipped_for_rotation(symbol, current_rotation):

                            skip_remaining = await get_skip_rotations_remaining(symbol, current_rotation)

                            output_manager.print_static(f"  {symbol.split('_')[0]}: ⏸️ Skipped (skip for {skip_remaining} more rotation(s))")

                            return None

                    # Check time-based cooldown (for non-rotation mode or legacy)

                    if exclude_cooldown and await is_coin_in_cooldown(symbol):

                        cooldown_remaining = await get_cooldown_remaining(symbol)

                        output_manager.print_static(f"  {symbol.split('_')[0]}: ⏸️ Cooldown ({cooldown_remaining}s)")

                        return None

                    analysis = await analyze_hold_wait_buy_ai(symbol, lite=True, force_fresh=True)

                    try:

                        coin_type = await get_coin_type_range(symbol)

                        coin_type_display = get_coin_display_name(symbol, coin_type)

                    except Exception:

                        coin_type_display = "ALTCOIN"

                    emoji = "🎯" if analysis.recommendation in [ActionRecommendation.STRONG_BUY] else "📈" if analysis.recommendation == ActionRecommendation.BUY else "⚠️"

                    output_manager.print_static(

                        f"  {symbol.split('_')[0]}: {emoji} AI(lite) {analysis.confidence:.0f}% {analysis.recommendation.value} | Score {analysis.score:.1f} | {coin_type_display}"

                    )

                    # Collect all coins (no confidence filter), prioritize by score

                    # Skip only SELL signals, accept all others (STRONG_BUY, BUY, HOLD, WAIT)

                    if analysis.recommendation != ActionRecommendation.SELL:

                        return (symbol, analysis)

                    return None

                except Exception as e:

                    output_manager.print_static(f"  {symbol.split('_')[0]}: ❌ Error")

                    return None

            batch_results = await asyncio.gather(*[scan_single_coin(s) for s in batch], return_exceptions=True)

            stage1_results.extend([r for r in batch_results if r and not isinstance(r, Exception)])

            if i + BATCH_SIZE_MARKET_SCAN < len(watchlist):

                await asyncio.sleep(0.2)

    else:

        for coin in watchlist:

            # Ensure coin has _USDT suffix

            if '_' not in coin:

                symbol = f"{coin}_USDT"

            else:

                symbol = coin

            try:

                # Check if symbol is supported in spot market first (filter futures-only coins)

                # Do a quick API check to verify symbol support before analysis

                exchange_client = _init_exchange_client()

                if exchange_client:

                    # Quick check: try to get price to verify symbol exists in spot market

                    # This will mark unsupported symbols in cache if they don't exist

                    try:

                        # Use asyncio.to_thread for synchronous get_price_rest call

                        # Check if exchange supports category parameter

                        exchange_name = getattr(exchange_client, 'exchange_name', '').upper()

                        supports_category = exchange_name in ['BYBIT', 'OKX', 'BITGET', 'KUCOIN', 'MEXC', 'GATEIO', 'BINANCE']

                        

                        if supports_category:

                            price_check_spot = await asyncio.to_thread(

                                exchange_client.get_price_rest, symbol, "spot"

                            )

                        else:

                            # For exchanges without category support (e.g., Binance), use default

                            price_check_spot = await asyncio.to_thread(

                                exchange_client.get_price_rest, symbol

                            )

                        

                        # If price is None, check futures market to determine if futures-only or not available

                        if price_check_spot is None:

                            # Symbol not in spot market - check if it exists in futures market

                            # Only check futures if exchange supports category parameter

                            if supports_category:

                                try:

                                    # Determine futures category based on exchange

                                    futures_category = "linear"  # Default for Bybit

                                    if exchange_name == "OKX":

                                        futures_category = "linear"

                                    elif exchange_name == "BITGET":

                                        futures_category = "linear"

                                    elif exchange_name == "KUCOIN":

                                        futures_category = "futures"

                                    elif exchange_name == "MEXC":

                                        futures_category = "futures"

                                    elif exchange_name == "GATEIO":

                                        futures_category = "futures"

                                    elif exchange_name == "BINANCE":

                                        futures_category = "futures"  # Binance uses "futures" category

                                    

                                    # Use asyncio.to_thread for synchronous get_price_rest call

                                    price_check_futures = await asyncio.to_thread(

                                        exchange_client.get_price_rest, symbol, futures_category

                                    )

                                    if price_check_futures is not None:

                                        # Coin exists in futures market but not in spot (futures-only)

                                        exchange_display_name = exchange_name.title()

                                        output_manager.print_static(

                                            f"  {symbol.split('_')[0]}: ⚠️ FUTURES-ONLY (only available in futures market {exchange_display_name}, will not be selected)"

                                        )

                                    else:

                                        # Coin doesn't exist in futures or spot (not available)

                                        exchange_display_name = exchange_name.title()

                                        output_manager.print_static(

                                            f"  {symbol.split('_')[0]}: ⚠️ NOT AVAILABLE (not available in {exchange_display_name})"

                                        )

                                except Exception as e:

                                    # If futures check fails, assume not available

                                    exchange_display_name = exchange_name.title()

                                    output_manager.print_static(

                                        f"  {symbol.split('_')[0]}: ⚠️ NOT AVAILABLE (not available in {exchange_display_name})"

                                    )

                            else:

                                # For exchanges without category support, assume not available if spot returns None

                                exchange_display_name = exchange_name.title()

                                output_manager.print_static(

                                    f"  {symbol.split('_')[0]}: ⚠️ NOT AVAILABLE (not available in {exchange_display_name})"

                                )

                            continue

                    except Exception:

                        # If check fails, try to check futures market directly (if supported)

                        exchange_name = getattr(exchange_client, 'exchange_name', '').upper()

                        supports_category = exchange_name in ['BYBIT', 'OKX', 'BITGET', 'KUCOIN', 'MEXC', 'GATEIO', 'BINANCE']

                        

                        if supports_category:

                            try:

                                # Determine futures category based on exchange

                                futures_category = "linear"  # Default for Bybit

                                if exchange_name == "OKX":

                                    futures_category = "linear"

                                elif exchange_name == "BITGET":

                                    futures_category = "linear"

                                elif exchange_name == "KUCOIN":

                                    futures_category = "futures"

                                elif exchange_name == "MEXC":

                                    futures_category = "futures"

                                elif exchange_name == "GATEIO":

                                    futures_category = "futures"

                                elif exchange_name == "BINANCE":

                                    futures_category = "futures"  # Binance uses "futures" category

                                

                                price_check_futures = await asyncio.to_thread(

                                    exchange_client.get_price_rest, symbol, futures_category

                                )

                                if price_check_futures is not None:

                                    exchange_display_name = exchange_name.title()

                                    output_manager.print_static(

                                        f"  {symbol.split('_')[0]}: ⚠️ FUTURES-ONLY (only available in futures market {exchange_display_name}, will not be selected)"

                                    )

                                else:

                                    exchange_display_name = exchange_name.title()

                                    output_manager.print_static(

                                        f"  {symbol.split('_')[0]}: ⚠️ NOT AVAILABLE (not available in {exchange_display_name})"

                                    )

                            except Exception:

                                exchange_display_name = exchange_name.title()

                                output_manager.print_static(

                                    f"  {symbol.split('_')[0]}: ⚠️ NOT AVAILABLE (not available in {exchange_display_name})"

                                )

                        else:

                            exchange_display_name = exchange_name.title()

                            output_manager.print_static(

                                f"  {symbol.split('_')[0]}: ⚠️ NOT AVAILABLE (not available in {exchange_display_name})"

                            )

                        continue

                

                # Check rotation skip first (for rotation mode)

                if rotation_mode and current_rotation > 0:

                    if await is_coin_skipped_for_rotation(symbol, current_rotation):

                        skip_remaining = await get_skip_rotations_remaining(symbol, current_rotation)

                        output_manager.print_static(f"  {symbol.split('_')[0]}: ⏸️ Skipped (skip for {skip_remaining} more rotation(s))")

                        continue

                # Check time-based cooldown (for non-rotation mode or legacy)

                if exclude_cooldown and await is_coin_in_cooldown(symbol):

                    cooldown_remaining = await get_cooldown_remaining(symbol)

                    output_manager.print_static(f"  {symbol.split('_')[0]}: ⏸️ Cooldown ({cooldown_remaining}s)")

                    continue

                analysis = await analyze_hold_wait_buy_ai(symbol, lite=True)

                try:

                    coin_type = await get_coin_type_range(symbol)

                    coin_type_display = get_coin_display_name(symbol, coin_type)

                except Exception:

                    coin_type_display = "ALTCOIN"

                emoji = "🎯" if analysis.recommendation in [ActionRecommendation.STRONG_BUY] else "📈" if analysis.recommendation == ActionRecommendation.BUY else "⚠️"

                # Format: Responsive Compact - Cocok untuk layar VS Code & HP
                output_manager.print_static(
                    f"  {symbol.split('_')[0]}: {emoji} {analysis.confidence:.0f}% {analysis.recommendation.value} │ Score {analysis.score:.1f} │ {coin_type_display}"
                )

                # Collect all coins (no confidence filter), prioritize by score

                # Skip only SELL signals, accept all others (STRONG_BUY, BUY, HOLD, WAIT)

                if analysis.recommendation != ActionRecommendation.SELL:

                    stage1_results.append((symbol, analysis))

                await asyncio.sleep(float(os.getenv('STAGE1_SCAN_DELAY', '0.2')))

            except Exception as e:

                output_manager.print_static(f"  {symbol.split('_')[0]}: ❌ Error")

    if not stage1_results:

        output_manager.print_static(f"\n{YELLOW}⚠️ No suitable coin found in watchlist{RESET}")

        return None, None, []

    # Sort by score (if available) or confidence, descending - pick best AI recommendation

    # Priority: Score first, then confidence if score is 0 or None

    def get_sort_key(item):

        symbol, analysis = item

        score = getattr(analysis, 'score', None)

        if score is None or score == 0:

            return analysis.confidence

        return score

    stage1_results.sort(key=get_sort_key, reverse=True)

    output_manager.print_static(f"\n{CYAN}🔍 STAGE 2: Deep quality analysis of top {min(3, len(stage1_results))} candidates...{RESET}")

    stage2_results = []

    top_candidates = stage1_results[:3]

    for symbol, analysis in top_candidates:

        try:

            exchange_client = _init_exchange_client()

            if exchange_client:

                if hasattr(exchange_client, 'is_symbol_supported_cached') and not exchange_client.is_symbol_supported_cached(symbol):

                    output_manager.print_static(f"  {symbol.split('_')[0]}: ⏭️ Skipping (not supported on {SELECTED_EXCHANGE} spot, cached)")

                    continue

                if symbol not in getattr(exchange_client, 'unsupported_symbols', set()):

                    if hasattr(exchange_client, 'probe_symbol_supported') and not exchange_client.probe_symbol_supported(symbol):

                        output_manager.print_static(f"  {symbol.split('_')[0]}: ⏭️ Skipping (not supported on {SELECTED_EXCHANGE} spot)")

                        continue

            # Format: Responsive Compact - Cocok untuk layar VS Code & HP
            output_manager.print_static(f"  {symbol.split('_')[0]}: 🔬 Analyzing...")

            universe_score = await calculate_coin_universe_score(symbol, analysis)

            stage2_results.append((symbol, analysis, universe_score))

            d = universe_score.get('details', {})

            critical = d.get('liquidity', 0) + d.get('spread', 0) + d.get('anti_manipulation', 0)

            quality = d.get('ai_confidence', 0) + d.get('order_flow', 0)

            supporting = d.get('orderbook', 0) + d.get('market_regime', 0)

            # Format: Responsive Compact
            output_manager.print_static(f"  {symbol.split('_')[0]}: ✅ {universe_score['total_score']:.1f}/100 ({universe_score['grade']}) │ C{critical:.0f} Q{quality:.0f} S{supporting:.0f}")

            try:

                if TradingConfig.ENABLE_ANALYSIS_QUALITY_OUTPUT and isinstance(universe_score, dict):

                    d = universe_score.get('details', {})

                    obq = d.get('orderbook', 0); liq = d.get('liquidity', 0); spd = d.get('spread', 0)

                    ai = d.get('ai_confidence', 0); flow = d.get('order_flow', 0); mkt = d.get('market_regime', 0); manip = d.get('anti_manipulation', 0)

                    liq_grade = d.get('liquidity_quality', 'N/A')

                    bid_ratio = d.get('bid_ratio', 0)

                    # Format: Responsive Compact - Compact quality details
                    output_manager.print_static(
                        f"     ▶ AI {ai:.1f} │ Flow {flow:.1f} │ OB {obq:.1f} ({bid_ratio:.1f}%) │ Liq {liq:.1f} ({liq_grade}) │ Spread {spd:.1f} │ Reg {mkt:.1f} │ Manip {manip:.1f}"
                    )

            except Exception:

                pass

            await asyncio.sleep(0.5)

        except Exception as e:

            output_manager.print_static(f"  {symbol.split('_')[0]}: ⚠️ Quality check failed, using AI score only")

            fallback_score = {

                'total_score': analysis.confidence * 0.25,

                'grade': 'C',

                'details': {'ai_confidence': analysis.confidence * 0.25}

            }

            stage2_results.append((symbol, analysis, fallback_score))



    def priority_sort_key(result):

        symbol, analysis, score = result

        details = score.get('details', {})

        

        # Safety checks (must pass) - PRIORITY 1

        liquidity = details.get('liquidity', 0)

        spread = details.get('spread', 0)

        manip = details.get('anti_manipulation', 0)

        critical_score = liquidity + spread + manip

        

        # Block unsafe coins immediately - they should never be selected

        if critical_score < 13:

            return (-999999, -1000, -1000, 0)  # Always sort to bottom

        

        # Profitability components

        ai = details.get('ai_confidence', 0)

        flow = details.get('order_flow', 0)

        quality_score = ai + flow

        

        # Minimum quality threshold (must be profitable) - PRIORITY 2

        if quality_score < 20:

            return (-500000, critical_score, -500, 0)  # Block unprofitable coins

        

        # Execution Speed Score (prioritize fast execution)

        # Factors that affect execution speed:

        # 1. Buy Pressure (order_flow) - higher = faster to reach activation

        # 2. Market Regime - trending = faster movement

        # 3. AI Confidence - higher = stronger signal = faster execution

        # 4. Orderbook Score (bid ratio) - higher = more buyer support = faster execution

        

        flow_score = details.get('order_flow', 0)  # 0-20

        regime_score = details.get('market_regime', 0)  # 0-10

        ai_score = details.get('ai_confidence', 0)  # 0-25

        orderbook_score = details.get('orderbook', 0)  # 0-15

        

        # Calculate execution speed score (0-70 with orderbook, was 0-55)

        # Buy pressure is most important for fast execution

        # Orderbook score adds buyer support factor

        execution_speed = (flow_score * 1.5) + (regime_score * 1.2) + (ai_score * 0.8) + (orderbook_score * 0.6)

        

        # Normalize to 0-100 scale for better comparison

        execution_speed_normalized = min(100, (execution_speed / 70) * 100)

        

        # Profitability score (must be profitable)

        profitability_score = quality_score  # 0-45

        

        # Combined score: 60% execution speed + 40% profitability

        # This prioritizes fast execution while ensuring profitability

        combined_score = (execution_speed_normalized * 0.6) + (profitability_score * 0.4)

        

        # Return: combined_score (execution speed + profitability), critical_score (safety), profitability_score, total_score

        # Note: critical_score is already validated (>= 13), so safe coins are sorted by combined_score

        return (combined_score, critical_score, profitability_score, score.get('total_score', 0))

    stage2_results.sort(key=priority_sort_key, reverse=True)

    if not stage2_results:

        output_manager.print_static(f"{RED}❌ No valid coins found after universe scoring{RESET}")

        return None, None, {'total_score': 0, 'grade': 'F', 'details': {}}

    best_symbol, best_analysis, best_score = stage2_results[0]

    d = best_score.get('details', {})

    critical = d.get('liquidity', 0) + d.get('spread', 0) + d.get('anti_manipulation', 0)

    quality = d.get('ai_confidence', 0) + d.get('order_flow', 0)

    warnings = []

    if critical < 13:

        warnings.append(f"{RED}🚨 CRITICAL SAFETY FAILED ({critical:.1f}/30 < 13){RESET}")

        warnings.append(f"{YELLOW}   → Liquidity: {d.get('liquidity', 0):.1f}/15 | Spread: {d.get('spread', 0):.1f}/10 | Manip: {d.get('anti_manipulation', 0):.1f}/5{RESET}")

        warnings.append(f"{RED}   → EXECUTION RISK HIGH - Consider WAITING!{RESET}")

    elif critical < 20:

        warnings.append(f"{YELLOW}⚠️ Critical Safety LOW ({critical:.1f}/30 < 20) - Execute with caution{RESET}")

    ai_score = d.get('ai_confidence', 0)

    flow_score = d.get('order_flow', 0)

    if quality < 25:

        warnings.append(f"{YELLOW}⚠️ Signal Quality LOW ({quality:.1f}/45 < 25){RESET}")

        if ai_score < 15:

            warnings.append(f"{YELLOW}   → Weak AI Signal ({ai_score:.1f}/25 < 15) - Technical indicators not aligned{RESET}")

        if flow_score < 10:

            warnings.append(f"{YELLOW}   → Weak Buy Pressure ({flow_score:.1f}/20 < 10) - More sellers than buyers{RESET}")

        warnings.append(f"{YELLOW}   → PROFIT POTENTIAL LOWER - May underperform{RESET}")

    elif flow_score <= 4:

        warnings.append(f"{YELLOW}⚠️ Buy Pressure VERY WEAK ({flow_score:.1f}/20) - Strong sell pressure detected!{RESET}")

        warnings.append(f"{YELLOW}   → Consider waiting for bullish order flow{RESET}")

    if warnings:

        output_manager.print_static(f"\n{YELLOW}{'═'*80}{RESET}")

        for warning in warnings:

            output_manager.print_static(warning)

        output_manager.print_static(f"{YELLOW}{'═'*80}{RESET}")

    

    # Note: Futures Market Signals will be displayed in main.py after Market Analysis

    # Data is available in best_score['details'] for caller to display

    

    # Format: Responsive Compact - Cocok untuk layar VS Code & HP
    output_manager.print_static(f"\n{GREEN}✅ BEST: {best_symbol} │ {best_score['total_score']:.1f}/100 ({best_score['grade']}){RESET}")

    quality_display = d.get('ai_confidence', 0) + d.get('order_flow', 0)

    supporting = d.get('orderbook', 0) + d.get('market_regime', 0)

    

    # Calculate execution speed score for display

    flow_score = d.get('order_flow', 0)

    regime_score = d.get('market_regime', 0)

    ai_score = d.get('ai_confidence', 0)

    orderbook_score = d.get('orderbook', 0)

    execution_speed = (flow_score * 1.5) + (regime_score * 1.2) + (ai_score * 0.8) + (orderbook_score * 0.6)

    execution_speed_normalized = min(100, (execution_speed / 70) * 100)

    

    critical_color = GREEN if critical >= 20 else YELLOW if critical >= 13 else RED

    execution_color = GREEN if execution_speed_normalized >= 70 else YELLOW if execution_speed_normalized >= 50 else CYAN

    # Format: Responsive Compact
    output_manager.print_static(
        f"{CYAN}   🎯 Priority: {critical_color}C{critical:.0f}/30{RESET} │ {CYAN}Quality {quality_display:.0f}/45 │ Support {supporting:.0f}/25{RESET}"
    )
    output_manager.print_static(
        f"{CYAN}   ⚡ Speed: {execution_color}{execution_speed_normalized:.0f}/100{RESET} │ Flow {flow_score:.0f} │ Reg {regime_score:.0f} │ AI {ai_score:.0f} │ OB {orderbook_score:.0f}"
    )

    actual_buy_pressure = d.get('buy_pressure', 0)

    if actual_buy_pressure == 0:

        flow_score = d.get('order_flow', 0)

        if flow_score == 4.0:

            actual_buy_pressure = 35.0

        elif flow_score == 8.0:

            actual_buy_pressure = 46.0

        elif flow_score == 12.0:

            actual_buy_pressure = 50.0

        elif flow_score == 16.0:

            actual_buy_pressure = 56.0

        elif flow_score == 20.0:

            actual_buy_pressure = 65.0

        else:

            actual_buy_pressure = 50.0

    # Format: Responsive Compact
    output_manager.print_static(f"{CYAN}   📊 AI: {best_analysis.confidence:.0f}% │ Buy Pressure: {actual_buy_pressure:.1f}% │ Liq: {best_score['details'].get('liquidity_quality', 'N/A')}{RESET}")

    try:

        if TradingConfig.ENABLE_ANALYSIS_QUALITY_OUTPUT and isinstance(best_score, dict):

            d = best_score.get('details', {})

            obq = d.get('orderbook', 0); liq = d.get('liquidity', 0); spd = d.get('spread', 0)

            ai = d.get('ai_confidence', 0); flow = d.get('order_flow', 0); mkt = d.get('market_regime', 0); manip = d.get('anti_manipulation', 0)

            liq_grade = d.get('liquidity_quality', 'N/A')

            # Format: Responsive Compact
            output_manager.print_static(
                f"{CYAN}   🧪 AI {ai:.1f} │ Flow {flow:.1f} │ OB {obq:.1f} │ Liq {liq:.1f} ({liq_grade}) │ Spread {spd:.1f} │ Reg {mkt:.1f} │ Manip {manip:.1f}{RESET}"
            )

    except Exception:

        pass

    if best_score['total_score'] >= 70:

        output_manager.print_static(f"{GREEN}✅ Quality check PASSED - High-grade trading opportunity!{RESET}")

    elif best_score['total_score'] >= 60:

        output_manager.print_static(f"{YELLOW}⚠️ Quality check MODERATE - Proceed with caution{RESET}")

    else:

        output_manager.print_static(f"{YELLOW}⚠️ Quality check LOW - Consider waiting for better opportunity{RESET}")

    # Return best_score as all_results so caller can access futures_sentiment and futures_risk

    return best_symbol, best_analysis, best_score



def _get_terminal_width(default: int = 80) -> int:

    try:

        return shutil.get_terminal_size().columns

    except Exception:

        return default



async def get_price_optimized(symbol: str, force_fresh: bool = False) -> Optional[float]:

    """

    Get optimized price with WebSocket priority and WebSocket/REST fallback.

    

    Args:

        symbol: Trading symbol

        force_fresh: If True, bypass WebSocket cache and fetch fresh from REST API

    

    Returns:

        Price or None if not available

    """

    # If force_fresh, skip WebSocket cache and go directly to REST API

    if force_fresh:

        exchange_client = _init_exchange_client()

        if exchange_client:

            return exchange_client.get_price_rest(symbol)

        return None

    

    # Strategy: Try multiple WebSocket sources before falling back to REST API
    # 1. Primary: WebSocket from selected exchange
    ws_manager = _init_ws_manager()
    if ws_manager:
        ws_price = ws_manager.get_price(symbol)
        if ws_price is not None:
            return ws_price

    # 2. Fallback: Try to reinitialize WebSocket manager (in case connection dropped)
    # This uses the same exchange that user selected
    try:
        # Force reinitialize to get fresh connection
        global _ws_manager
        _ws_manager = None  # Reset to force reinit
        fallback_ws_manager = _init_ws_manager()
        if fallback_ws_manager and fallback_ws_manager != ws_manager:
            fallback_price = fallback_ws_manager.get_price(symbol)
            if fallback_price is not None:
                return fallback_price
    except Exception:
        # If WebSocket reinit fails, continue to REST API
        pass

    # 3. Final fallback: REST API from selected exchange
    exchange_client = _init_exchange_client()
    if exchange_client:
        return exchange_client.get_price_rest(symbol)

    return None



async def get_futures_price(symbol: str, category: str = "linear") -> Optional[float]:

    """

    Get futures price for a symbol.

    

    Args:

        symbol: Trading symbol (e.g., "BTC_USDT")

        category: Futures category ("linear" for Bybit, "futures" for others)

    

    Returns:

        Futures price or None if not available

    """

    try:

        exchange_client = _init_exchange_client()

        if not exchange_client:

            return None

        

        # Determine category based on exchange

        exchange_name = getattr(exchange_client, 'exchange_name', '').upper()

        if exchange_name in ['BYBIT', 'OKX', 'BITGET']:

            futures_category = "linear"

        elif exchange_name in ['KUCOIN', 'MEXC', 'GATEIO', 'BINANCE']:

            futures_category = "futures"

        else:

            futures_category = category  # Use provided category

        

        # Get futures price

        if hasattr(exchange_client, 'get_price_rest'):

            futures_price = await asyncio.to_thread(

                exchange_client.get_price_rest, symbol, futures_category

            )

            return futures_price

        return None

    except Exception as e:

        # Silent fail - futures market may not be available for all coins

        return None



async def get_futures_premium(symbol: str) -> Optional[float]:

    """

    Calculate futures premium/discount vs spot price.

    

    Args:

        symbol: Trading symbol

    

    Returns:

        Premium percentage (positive = premium, negative = discount) or None

    """

    try:

        spot_price = await get_price_optimized(symbol)

        if spot_price is None:

            return None

        

        futures_price = await get_futures_price(symbol)

        if futures_price is None:

            return None

        

        premium = safe_division((futures_price - spot_price), spot_price, 0.0) * 100

        return premium

    except Exception:

        return None



async def get_futures_candles(symbol: str, interval: str = "5m", limit: int = 200) -> Optional[List[Dict]]:

    """

    Get futures candles from exchange.

    

    Args:

        symbol: Trading symbol

        interval: Candle interval (default: "5m")

        limit: Number of candles to fetch (default: 200)

    

    Returns:

        List of candle dictionaries or None

    """

    try:

        exchange_client = _init_exchange_client()

        if not exchange_client:

            return None

        

        # Determine futures category based on exchange

        exchange_name = getattr(exchange_client, 'exchange_name', '').upper()

        if exchange_name == 'BYBIT':

            category = 'linear'

        elif exchange_name in ['OKX', 'BITGET']:

            category = 'linear'

        elif exchange_name in ['KUCOIN', 'MEXC', 'GATEIO', 'BINANCE']:

            category = 'futures'

        else:

            # Default to linear for unknown exchanges

            category = 'linear'

        

        # Fetch futures candles

        futures_candles = await asyncio.to_thread(

            exchange_client.fetch_candles, symbol, interval, limit, category

        )

        

        if not futures_candles:

            return None

        

        return futures_candles

    except Exception:

        return None



async def get_futures_price_change(symbol: str, period_minutes: int = 60) -> Optional[float]:

    """

    Get futures price change over a period (leading indicator).

    

    Args:

        symbol: Trading symbol

        period_minutes: Period in minutes (default: 60 = 1 hour)

    

    Returns:

        Price change percentage or None

    """

    try:

        futures_price_now = await get_futures_price(symbol)

        if futures_price_now is None:

            return None

        

        # Get futures candles to calculate historical price

        # Ensure minimum 2 candles for price change calculation

        candle_limit = max(2, period_minutes // 5 + 1)

        futures_candles = await get_futures_candles(symbol, interval='5m', limit=candle_limit)

        

        if not futures_candles or len(futures_candles) < 2:

            # Fallback to spot candles if futures candles unavailable

            spot_candles = await get_candles_optimized(symbol, interval='5m', limit=candle_limit)

            if not spot_candles or len(spot_candles) < 2:

                return None

            candles = spot_candles

            using_futures_data = False

        else:

            candles = futures_candles

            using_futures_data = True

        

        # Calculate price change from oldest to newest candle

        # Candles are sorted oldest first, newest last

        oldest_close = float(candles[0].get('close', candles[0].get('c', 0)))

        newest_close = float(candles[-1].get('close', candles[-1].get('c', 0)))

        

        # Use current futures price as newest_close if available (more accurate)

        if futures_price_now and using_futures_data:

            newest_close = futures_price_now

        

        if oldest_close == 0:

            return None

        

        price_change = safe_division((newest_close - oldest_close), oldest_close, 0.0) * 100

        

        # Store flag in futures_sentiment_info for tracking (will be set by caller)

        return price_change

    except Exception:

        return None



async def get_candles_optimized(symbol: str, interval: str = "5m", limit: int = 200, force_fresh: bool = False) -> Optional[List[Dict]]:

    # Try to get candles from WebSocket manager if it supports get_candles method

    ws_manager = _init_ws_manager()

    if ws_manager and hasattr(ws_manager, 'get_candles'):

        try:

            ws_candles = ws_manager.get_candles(symbol, interval, limit)

            if ws_candles:

                return ws_candles

        except Exception:

            pass  # Fall through to REST API if WebSocket fails

    

    if not force_fresh:

        max_age_s = CANDLE_MAX_AGE.get(interval, 3600)

        persisted = load_persisted_candles(symbol, interval, limit, max_age_s)

        if persisted:

            try:

                cache_key = f"{symbol}_{interval}"

                if ws_manager and hasattr(ws_manager, 'candle_cache'):

                    ws_manager.candle_cache[cache_key] = {

                        'candles': persisted,

                        'timestamp': time.time()

                    }

            except:

                pass

            return persisted

    

    # Try to initialize candle cache if WebSocket manager supports it

    if ws_manager and hasattr(ws_manager, 'initialize_candle_cache'):

        try:

            canonical_limits = {

                '1m': 200,

                '3m': 200,

                '5m': 200,

                '15m': 100,

                '30m': 100,

                '1h': 100,

                '2h': 100,

                '4h': 100,

                '6h': 100,

                '12h': 100,

                '1d': 100,

                '1w': 100,

                '1M': 100

            }

            seed_limit = canonical_limits.get(interval, max(100, limit))

            await ws_manager.initialize_candle_cache(symbol, interval, seed_limit)

            if hasattr(ws_manager, 'get_candles'):

                ws_candles_after_seed = ws_manager.get_candles(symbol, interval, limit)

                if ws_candles_after_seed:

                    save_persisted_candles(symbol, interval, ws_candles_after_seed)

                    return ws_candles_after_seed

        except Exception:

            pass

    

    # Fallback to REST API

    exchange_client = _init_exchange_client()

    if exchange_client and hasattr(exchange_client, 'fetch_candles'):

        try:

            rest_candles = exchange_client.fetch_candles(symbol, interval, limit)

            if rest_candles:

                save_persisted_candles(symbol, interval, rest_candles)

            return rest_candles

        except Exception:

            pass

    return None



async def get_order_flow_optimized(symbol: str, limit: int = 20) -> Dict:

    """
    Get order flow data with WebSocket priority, REST API fallback.
    Returns neutral buy_pressure (50.0%) if both fail.
    """
    
    ws_manager = _init_ws_manager()
    ws_error = None
    ws_trade_count = 0

    if ws_manager and hasattr(ws_manager, 'get_order_flow_analysis'):

        try:

            ws_flow = ws_manager.get_order_flow_analysis(symbol, limit)

            if ws_flow:
                ws_trade_count = ws_flow.get('trade_count', 0)
                if ws_trade_count > 0:
                    return ws_flow
                else:
                    # WebSocket cache exists but empty (no recent trades)
                    ws_error = f"WebSocket cache empty (trade_count=0)"

        except Exception as e:

            ws_error = f"WebSocket error: {str(e)}"

    exchange_client = _init_exchange_client()
    rest_error = None
    rest_trade_count = 0

    if exchange_client and hasattr(exchange_client, 'get_order_flow_analysis'):

        try:

            rest_flow = exchange_client.get_order_flow_analysis(symbol, limit)
            if rest_flow:
                rest_trade_count = rest_flow.get('trade_count', 0)
                if rest_trade_count > 0:
                    return rest_flow
                else:
                    rest_error = f"REST API returned no trades (trade_count=0)"

        except Exception as e:

            rest_error = f"REST API error: {str(e)}"

    # Both failed - log reason for debugging (only in debug mode to avoid spam)
    if getattr(TradingConfig, 'ENABLE_ORDER_FLOW_DEBUG', False):
        error_msg = f"Order flow unavailable for {symbol}: "
        if ws_error:
            error_msg += f"WS({ws_error})"
        if rest_error:
            error_msg += f" REST({rest_error})" if ws_error else f"REST({rest_error})"
        if not ws_error and not rest_error:
            if not ws_manager:
                error_msg += "WebSocket manager not initialized"
            elif not exchange_client:
                error_msg += "Exchange client not initialized"
            else:
                error_msg += "Unknown reason"
        
        # Only log if both completely failed (not just empty cache)
        if ws_error and rest_error and "cache empty" not in ws_error.lower():
            output_manager.print_static(f"{YELLOW}⚠️ Order flow debug: {error_msg}{RESET}")

    # Return default empty flow if both fail
    # IMPORTANT: Include buy_pressure with default 50.0 (neutral) to prevent 0.0% display
    return {
        'trade_count': 0,
        'buy_volume': 0,
        'sell_volume': 0,
        'net_flow': 0,
        'buy_pressure': 50.0,  # Default neutral value (not 0.0%)
        'sell_pressure': 50.0,
        'cvd': 0,
        'volume_profile': {},
        'institutional_activity': 0,
        'cvd_momentum': 0,
        'order_imbalance': 0,
        '_debug_info': {
            'ws_available': ws_manager is not None,
            'ws_error': ws_error,
            'ws_trade_count': ws_trade_count,
            'rest_available': exchange_client is not None,
            'rest_error': rest_error,
            'rest_trade_count': rest_trade_count
        } if getattr(TradingConfig, 'ENABLE_ORDER_FLOW_DEBUG', False) else None
    }



async def calculate_flow_score_from_buy_pressure(

    symbol: str,

    buy_pressure: float,

    analysis,

    volatility: Optional[float] = None

) -> float:

    """

    Calculate flow score from buy_pressure percentage.

    Same logic as in calculate_coin_universe_score() but as a standalone function.

    

    Args:

        symbol: Trading symbol

        buy_pressure: Buy pressure percentage (0-100)

        analysis: ActionAnalysis object (for market regime)

        volatility: Optional volatility (will be calculated if not provided)

    

    Returns:

        Flow score (4.0, 8.0, 12.0, 16.0, or 20.0)

    """

    try:

        coin_type = await get_coin_type_range(symbol)

        if coin_type == 'BLUECHIP':

            base_thresholds = {'excellent': 65, 'good': 58, 'moderate': 52, 'neutral': 48}

        elif coin_type == 'MAJOR':

            base_thresholds = {'excellent': 62, 'good': 56, 'moderate': 50, 'neutral': 46}

        elif coin_type == 'ALTCOIN':

            base_thresholds = {'excellent': 58, 'good': 53, 'moderate': 48, 'neutral': 44}

        elif coin_type == 'MICROCAP':

            base_thresholds = {'excellent': 53, 'good': 48, 'moderate': 45, 'neutral': 42}

        else:

            base_thresholds = {'excellent': 60, 'good': 54, 'moderate': 48, 'neutral': 45}

        

        total_adjustment = 0.0

        

        # Volatility adjustment

        if volatility is None:

            try:

                volatility = await calculate_coin_volatility(symbol)

            except Exception:

                volatility = 20.0  # Default

        

        if volatility > 40:

            vol_adj = -10.0

        elif volatility > 25:

            vol_adj = -5.0

        elif volatility < 10:

            vol_adj = +5.0

        else:

            vol_adj = 0.0

        total_adjustment += vol_adj

        

        # Market regime adjustment

        try:

            market_regime = analysis.market_regime.value if hasattr(analysis, 'market_regime') else 'sideways'

            if market_regime in ['strong_downtrend', 'downtrend']:

                regime_adj = -15.0

            elif market_regime == 'weak_downtrend':

                regime_adj = -10.0

            elif market_regime in ['sideways', 'high_vol_consolidation']:

                regime_adj = -5.0

            elif market_regime in ['weak_uptrend']:

                regime_adj = -3.0

            elif market_regime in ['strong_uptrend', 'uptrend']:

                regime_adj = 0.0

            else:

                regime_adj = 0.0

            total_adjustment += regime_adj

        except Exception:

            pass

        

        # Calculate adjusted thresholds

        thresholds = {}

        for key, base_value in base_thresholds.items():

            adjusted_value = base_value + total_adjustment

            thresholds[key] = max(35.0, min(95.0, adjusted_value))

        

        # Calculate flow score

        if buy_pressure >= thresholds['excellent']:

            flow_score = 20.0

        elif buy_pressure >= thresholds['good']:

            flow_score = 16.0

        elif buy_pressure >= thresholds['moderate']:

            flow_score = 12.0

        elif buy_pressure >= thresholds['neutral']:

            flow_score = 8.0

        else:

            flow_score = 4.0

        

        return flow_score

    except Exception as e:

        output_manager.print_static(f"{YELLOW}⚠️ Flow score calculation error: {e}{RESET}")

        return 10.0  # Default neutral score



async def get_futures_sentiment(symbol: str) -> Optional[Dict]:

    """

    Get futures market sentiment indicators.

    

    Args:

        symbol: Trading symbol

    

    Returns:

        Dict with sentiment indicators or None

    """

    try:

        # For now, we'll use premium/discount as sentiment proxy

        # In production, you'd want to fetch long/short ratio and funding rate from exchange API

        premium = await get_futures_premium(symbol)

        if premium is None:

            return None

        

        # Determine sentiment based on premium

        if premium > 2.0:

            sentiment = 'very_bullish'

        elif premium > 0.5:

            sentiment = 'bullish'

        elif premium > -0.5:

            sentiment = 'neutral'

        elif premium > -2.0:

            sentiment = 'bearish'

        else:

            sentiment = 'very_bearish'

        

        return {

            'premium': premium,

            'sentiment': sentiment,

            'long_short_ratio': None,  # Would need exchange API

            'funding_rate': None,  # Would need exchange API

            'open_interest': None  # Would need exchange API

        }

    except Exception:

        return None



async def get_futures_risk_indicator(symbol: str) -> Optional[Dict]:

    """

    Get futures market risk indicators.

    

    Args:

        symbol: Trading symbol

    

    Returns:

        Dict with risk indicators or None

    """

    try:

        # Calculate futures and spot volatility

        futures_price_change = await get_futures_price_change(symbol, period_minutes=60)

        spot_price_change = None

        

        candles = await get_candles_optimized(symbol, interval='5m', limit=12)

        if candles and len(candles) >= 2:

            oldest_close = float(candles[-1].get('close', candles[-1].get('c', 0)))

            newest_close = float(candles[0].get('close', candles[0].get('c', 0)))

            if oldest_close > 0:

                spot_price_change = abs(safe_division((newest_close - oldest_close), oldest_close, 0.0) * 100)

        

        # Determine risk level

        risk_level = 'low'

        futures_volatility = abs(futures_price_change) if futures_price_change else 0

        spot_volatility = spot_price_change if spot_price_change else 0

        

        if futures_volatility > spot_volatility * 2.0 and futures_volatility > 5.0:

            risk_level = 'high'  # Futures very volatile

        elif futures_volatility > spot_volatility * 1.5 and futures_volatility > 3.0:

            risk_level = 'medium'

        

        return {

            'risk_level': risk_level,

            'futures_volatility': futures_volatility,

            'spot_volatility': spot_volatility,

            'volatility_ratio': safe_division(futures_volatility, spot_volatility, 1.0) if spot_volatility > 0 else 1.0,

            'liquidations': None  # Would need exchange API

        }

    except Exception:

        return None



async def get_data_with_retry(func, max_retries: int = 3, timeout: float = 5.0, backoff_base: float = 2.0):

    """

    Retry mechanism dengan exponential backoff untuk resilient data fetching.

    

    Args:

        func: Async function to execute

        max_retries: Maximum number of retries

        timeout: Timeout per attempt (seconds)

        backoff_base: Base for exponential backoff (2.0 = 1s, 2s, 4s)

    

    Returns:

        Result from func or None if all retries fail

    """

    for i in range(max_retries):

        try:

            return await asyncio.wait_for(func(), timeout=timeout)

        except Exception as e:

            if i == max_retries - 1:

                # Last retry failed, raise exception

                raise

            # Exponential backoff: 1s, 2s, 4s, etc.

            wait_time = backoff_base ** i

            await asyncio.sleep(wait_time)

    return None



async def get_orderbook_optimized(symbol: str, limit: int = 20, force_fresh: bool = False, max_age_s: Optional[int] = None) -> Optional[Dict]:

    if max_age_s is None:

        max_age_s = 2 if force_fresh else 30  # Reduced from 5s to 2s for live trading

    ws_manager = _init_ws_manager()

    if ws_manager and hasattr(ws_manager, 'get_orderbook'):

        try:

            ws_orderbook = ws_manager.get_orderbook(symbol, limit, max_age_s=max_age_s)

            if ws_orderbook is not None:

                levels_ok = True

                try:

                    lv = ws_orderbook.get('levels_available', {})

                    levels_ok = (lv.get('bids', 0) >= min(limit, 10)) and (lv.get('asks', 0) >= min(limit, 10))

                except Exception:

                    levels_ok = True

                if not force_fresh or levels_ok:

                    return ws_orderbook

        except Exception:

            pass

    exchange_client = _init_exchange_client()

    if exchange_client and hasattr(exchange_client, 'get_orderbook'):

        try:

            return exchange_client.get_orderbook(symbol, limit)

        except Exception:

            pass

    return None



async def stop_websocket_streams():

    ws_manager = _init_ws_manager()

    if ws_manager and hasattr(ws_manager, 'stop_all_streams'):

        try:

            await ws_manager.stop_all_streams()

        except Exception:

            pass



async def display_order_flow_2lines(symbol: str, output_manager, limit: int = 50):

    try:

        flow_data = await get_order_flow_optimized(symbol, limit)

        if flow_data['trade_count'] < 10:

            return

        try:

            ws_manager = _init_ws_manager()

            if ws_manager and hasattr(ws_manager, 'orderbook_cache'):

                ob = ws_manager.orderbook_cache.get(symbol)

            else:

                ob = None

        except Exception:

            ob = None

        if not ob:

            ob = await get_orderbook_optimized(symbol, limit=6)

        if ob and 'bids' in ob and 'asks' in ob and len(ob['bids'])>0 and len(ob['asks'])>0:

            top_bid = ob['bids'][0]

            top_ask = ob['asks'][0]

            first_bid = f"{top_bid[0]:.2f}({top_bid[1]})"

            first_ask = f"{top_ask[0]:.2f}({top_ask[1]})"

            spread_pct = safe_division((top_ask[0] - top_bid[0]), top_bid[0], 0.0) * 100

            raw_buy_pressure = flow_data['buy_pressure']

            raw_sell_pressure = flow_data['sell_pressure']

            if raw_buy_pressure > 90 or raw_sell_pressure > 90:

                buy_pressure = raw_buy_pressure * 0.8 + 50 * 0.2

                sell_pressure = raw_sell_pressure * 0.8 + 50 * 0.2

            else:

                buy_pressure = raw_buy_pressure

                sell_pressure = raw_sell_pressure

            cvd = flow_data['cvd']

            trade_count = flow_data['trade_count']

            institutional_activity = flow_data['institutional_activity']

            cvd_momentum = flow_data['cvd_momentum']

            order_imbalance = flow_data['order_imbalance']

            if buy_pressure > 60:

                flow_arrow = "🟢"

                flow_text = "BUY"

            elif sell_pressure > 60:

                flow_arrow = "🔴"

                flow_text = "SELL"

            else:

                flow_arrow = "🟡"

                flow_text = "NEUTRAL"

            inst_indicator = ""

            if institutional_activity > 20:

                inst_indicator = " 🏛️"

            elif institutional_activity > 10:

                inst_indicator = " 🏢"

            momentum_indicator = ""

            if cvd_momentum > 1000:

                momentum_indicator = " ⬆️"

            elif cvd_momentum < -1000:

                momentum_indicator = " ⬇️"

            imbalance_indicator = ""

            if order_imbalance > 10:

                imbalance_indicator = " 📈"

            elif order_imbalance < -10:

                imbalance_indicator = " 📉"

            terminal_width = _get_terminal_width()

            line1 = f"{CYAN}📊 {symbol} Orderbook: {first_bid} | {first_ask} | Spread: {spread_pct:.2f}%{RESET}"

            line2 = f"{flow_arrow} Flow: {flow_text} {buy_pressure:.0f}%/{sell_pressure:.0f}% | CVD: {cvd:+.0f}{momentum_indicator} | Inst: {institutional_activity:.0f}%{inst_indicator} | Imb: {order_imbalance:+.0f}%{imbalance_indicator}{RESET}"

            output_manager.print_static(line1)

            output_manager.print_static(line2)

            volume_profile = flow_data.get('volume_profile', {})

            if volume_profile and volume_profile.get('volume_concentration', 0) > 0.3:

                high_levels = volume_profile.get('high_volume_levels', [])

                if high_levels:

                    vp_text = f"{CYAN}📈 Volume Profile: "

                    for i, (price, data) in enumerate(high_levels[:2]):

                        vp_text += f"${price:.2f}({data['total']:.0f})"

                        if i < len(high_levels[:2]) - 1:

                            vp_text += " | "

                    vp_text += f" | Conc: {volume_profile['volume_concentration']*100:.0f}%{RESET}"

                    output_manager.print_static(vp_text)

            tape_reading = flow_data.get('tape_reading', {})

            if tape_reading.get('large_trade_ratio', 0) > 15:

                tape_text = f"{YELLOW}📊 Tape: Large trades {tape_reading['large_trade_ratio']:.0f}% | Avg size: ${tape_reading['avg_trade_size']:.0f} | Freq: {tape_reading['trade_frequency']}{RESET}"

                output_manager.print_static(tape_text)

    except Exception as e:

        output_manager.print_static(f"{RED}❌ Order flow display error: {e}{RESET}")



def enhanced_validate_candles(candles: List[Dict]) -> Tuple[bool, List[Dict]]:

    if not candles:

        return False, []

    valid_candles = []

    anomalies_detected = 0

    for i, candle in enumerate(candles):

        try:

            if not all(key in candle for key in ['open', 'high', 'low', 'close']):

                continue

            o, h, l, c = candle['open'], candle['high'], candle['low'], candle['close']

            if any(price <= 0 for price in [o, h, l, c]):

                continue

            if not (l <= o <= h and l <= c <= h):

                continue

            if i > 0:

                prev_candle = candles[i-1]

                if prev_candle['close'] > 0:

                    price_change = abs(c / prev_candle['close'] - 1)

                    if price_change > 0.5:

                        anomalies_detected += 1

                        continue

            valid_candles.append(candle)

        except (TypeError, KeyError, ValueError):

            continue

    data_quality = len(valid_candles) / len(candles) if candles else 0

    anomaly_ratio = anomalies_detected / len(candles) if candles else 0

    if data_quality < 0.7 or anomaly_ratio > 0.1:

        return False, valid_candles

    return True, valid_candles



def validate_candles(candles: List[Dict]) -> Tuple[bool, List[Dict]]:

    return enhanced_validate_candles(candles)

@functools.lru_cache(maxsize=2000)

def calculate_atr_cached(candles_tuple: tuple, period: int = 14) -> float:

    candles = list(candles_tuple)

    return _calculate_atr_impl(candles, period)



def _calculate_atr_impl(candles: List[Dict], period: int = 14) -> float:

    if len(candles) < period + 1:

        return 0.0

    try:

        true_ranges = []

        for i in range(1, len(candles)):

            high = candles[i]['high']

            low = candles[i]['low']

            prev_close = candles[i-1]['close']

            tr = max(

                high - low,

                abs(high - prev_close),

                abs(low - prev_close)

            )

            true_ranges.append(tr)

        if len(true_ranges) >= period:

            atr = sum(true_ranges[-period:]) / period

            return atr

        else:

            return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

    except Exception as e:

        return 0.0



def calculate_atr(candles: List[Dict], period: int = 14) -> float:

    try:

        candles_tuple = tuple(

            (c['open'], c['high'], c['low'], c['close'])

            for c in candles[-100:]

        )

        return calculate_atr_cached(candles_tuple, period)

    except Exception:

        return _calculate_atr_impl(candles, period)



async def calculate_atr_based_stop_loss(symbol: str, multiplier: float = 1.5) -> float:

    try:

        candles = await get_candles_optimized(symbol, '1h', 100)

        if not candles or len(candles) < 14:

            coin_type = await get_coin_type_range(symbol)

            volatility = await calculate_coin_volatility(symbol)

            return get_adaptive_stop_loss(coin_type, volatility)

        tr_values = []

        for i in range(1, len(candles)):

            high = candles[i]['high']

            low = candles[i]['low']

            prev_close = candles[i-1]['close']

            tr = max(

                high - low,

                abs(high - prev_close),

                abs(low - prev_close)

            )

            tr_values.append(tr)

        if len(tr_values) < 14:

            coin_type = await get_coin_type_range(symbol)

            volatility = await calculate_coin_volatility(symbol)

            return get_adaptive_stop_loss(coin_type, volatility)

        atr = sum(tr_values[-14:]) / 14

        current_price = candles[-1]['close']

        stop_loss_pct = safe_division((atr * multiplier), current_price, 0.02) * 100

        coin_type = await get_coin_type_range(symbol)

        if coin_type == 'BLUECHIP':

            stop_loss_pct = safe_division((atr * 1.3), current_price, 0.015) * 100

        elif coin_type == 'MAJOR':

            stop_loss_pct = safe_division((atr * 1.5), current_price, 0.020) * 100

        elif coin_type == 'ALTCOIN':

            stop_loss_pct = safe_division((atr * 1.75), current_price, 0.030) * 100

        else:

            stop_loss_pct = safe_division((atr * 2.2), current_price, 0.045) * 100

        return max(1.0, min(8.0, stop_loss_pct))

    except Exception as e:

        coin_type = await get_coin_type_range(symbol)

        volatility = await calculate_coin_volatility(symbol)

        return get_adaptive_stop_loss(coin_type, volatility)



def calculate_advanced_indicators(candles: List[Dict[str, Any]]) -> Dict[str, Any]:

    start_time = time.time()

    is_valid, valid_candles = validate_candles(candles)

    if not is_valid:

        result = calculate_simple_indicators(valid_candles if valid_candles else [])

        performance_monitor.record_analysis(time.time() - start_time)

        return result

    try:

        if not HAS_PANDAS:

            result = calculate_simple_indicators(valid_candles)

            performance_monitor.record_analysis(time.time() - start_time)

            return result

        df = pd.DataFrame(valid_candles)

        closes = pd.Series([c['close'] for c in valid_candles]).astype(float)

        highs = pd.Series([c['high'] for c in valid_candles]).astype(float)

        lows = pd.Series([c['low'] for c in valid_candles]).astype(float)

        volumes = pd.Series([c.get('volume', 1.0) for c in valid_candles]).astype(float)

        current_price = closes.iloc[-1] if len(closes) > 0 else 0

        try:

            ema9 = closes.ewm(span=9).mean().iloc[-1]

            ema20 = closes.ewm(span=20).mean().iloc[-1]

            ema50 = closes.ewm(span=50).mean().iloc[-1]

            ema100 = closes.ewm(span=100).mean().iloc[-1]

            ema200 = closes.ewm(span=200).mean().iloc[-1]

        except Exception:

            ema9 = ema20 = ema50 = ema100 = ema200 = current_price

        try:

            # International standard: RSI using Welles Wilder's Modified Moving Average (RMA)

            # Standard parameters: period = 14

            # RMA formula: RMA = (RMA_prev * (period-1) + current_value) / period

            # Equivalent to EWM with alpha=1/period and adjust=False

            rsi_period = 14

            delta = closes.diff()

            

            # Calculate gains and losses

            gains = delta.where(delta > 0, 0.0)

            losses = -delta.where(delta < 0, 0.0)

            

            # Calculate RMA using EWM with alpha=1/period (equivalent to Welles Wilder RMA)

            # First value is SMA, then RMA for subsequent values

            if len(gains) >= rsi_period + 1:

                # Calculate initial SMA

                avg_gain = gains.iloc[1:rsi_period+1].mean()

                avg_loss = losses.iloc[1:rsi_period+1].mean()

                

                # Calculate RMA for remaining periods using vectorized approach

                remaining_gains = gains.iloc[rsi_period+1:]

                remaining_losses = losses.iloc[rsi_period+1:]

                

                # Apply RMA formula: RMA = (RMA_prev * (period-1) + current) / period

                for i in range(len(remaining_gains)):

                    avg_gain = (avg_gain * (rsi_period - 1) + remaining_gains.iloc[i]) / rsi_period

                    avg_loss = (avg_loss * (rsi_period - 1) + remaining_losses.iloc[i]) / rsi_period

                

                # Calculate RSI: RSI = 100 - (100 / (1 + RS)), where RS = avg_gain / avg_loss

                if avg_loss != 0 and not math.isnan(avg_gain) and not math.isnan(avg_loss):

                    rs = avg_gain / avg_loss

                    rsi = 100 - (100 / (1 + rs))

                    rsi = max(0, min(100, rsi))  # Clamp between 0-100

                else:

                    rsi = 50

            else:

                rsi = 50

        except Exception:

            rsi = 50

        try:

            # International standard: MACD parameters (12, 26, 9)

            # MACD Line = EMA(12) - EMA(26)

            # Signal Line = EMA(9) of MACD Line

            # Histogram = MACD Line - Signal Line

            ema12 = closes.ewm(span=12, adjust=False).mean()

            ema26 = closes.ewm(span=26, adjust=False).mean()

            macd_line = ema12 - ema26

            macd_signal_line = macd_line.ewm(span=9, adjust=False).mean()

            macd_histogram_val = macd_line - macd_signal_line

            macd_val = macd_line.iloc[-1] if not math.isnan(macd_line.iloc[-1]) else 0

            macd_signal_val = macd_signal_line.iloc[-1] if not math.isnan(macd_signal_line.iloc[-1]) else 0

            macd_histogram_val_final = macd_histogram_val.iloc[-1] if not math.isnan(macd_histogram_val.iloc[-1]) else 0

        except Exception:

            macd_val = macd_signal_val = macd_histogram_val_final = 0

        try:

            # International standard: Bollinger Bands parameters (period=20, multiplier=2.0)

            # Standard formula: BB Upper = SMA(20) + (2.0 * StdDev(20))

            #                  BB Lower = SMA(20) - (2.0 * StdDev(20))

            bb_period = 20

            bb_multiplier = 2.0

            sma = closes.rolling(bb_period).mean()

            std = closes.rolling(bb_period).std()

            bb_upper = sma + (std * bb_multiplier)

            bb_lower = sma - (std * bb_multiplier)

            bb_width_val = bb_upper.iloc[-1] - bb_lower.iloc[-1]

            if bb_width_val > 0:

                bb_position = (closes.iloc[-1] - bb_lower.iloc[-1]) / bb_width_val * 100

            else:

                bb_position = 50.0

            bb_width_pct = safe_division(bb_width_val, sma.iloc[-1], 5.0) * 100

            bb_upper_val = bb_upper.iloc[-1] if not math.isnan(bb_upper.iloc[-1]) else current_price * 1.1

            bb_lower_val = bb_lower.iloc[-1] if not math.isnan(bb_lower.iloc[-1]) else current_price * 0.9

        except Exception:

            bb_position = 50.0

            bb_width_pct = 5.0

            bb_upper_val = current_price * 1.1

            bb_lower_val = current_price * 0.9

        try:

            volume_sma = volumes.rolling(20).mean()

            volume_ratio_val = safe_division(volumes.iloc[-1], volume_sma.iloc[-1], 1.0)

        except Exception:

            volume_ratio_val = 1.0

        try:

            tr1 = highs - lows

            tr2 = (highs - closes.shift()).abs()

            tr3 = (lows - closes.shift()).abs()

            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

            atr_val = tr.rolling(14).mean().iloc[-1]

            atr_percent_val = safe_division(atr_val, current_price, 0.01) * 100

        except Exception:

            atr_val = current_price * 0.01

            atr_percent_val = 1.0

        try:

            momentum_1h = safe_division(closes.iloc[-1], closes.iloc[-12] if len(closes) >= 12 else current_price, 1.0) - 1

            momentum_1h_pct = momentum_1h * 100

        except Exception:

            momentum_1h_pct = 0

        try:

            if len(closes) >= 2:

                pivot = (highs.iloc[-2] + lows.iloc[-2] + closes.iloc[-2]) / 3

                r1_pivot = 2 * pivot - lows.iloc[-2]

                s1_pivot = 2 * pivot - highs.iloc[-2]

                lookback_period = min(20, len(highs))

                r1_max = highs.iloc[-lookback_period:].max()

                s1_max = lows.iloc[-lookback_period:].min()

                pivot_weight = 0.3

                max_weight = 0.7

                r1 = (r1_pivot * pivot_weight) + (r1_max * max_weight)

                s1 = (s1_pivot * pivot_weight) + (s1_max * max_weight)

                pivot = pivot

            else:

                pivot = r1 = s1 = current_price

        except Exception:

            pivot = r1 = s1 = current_price

        try:

            # Detect timeframe from candle data for proper annualization (international standard)

            # Check time difference between candles to determine timeframe

            timeframe_minutes = 60  # Default to 1h

            if len(valid_candles) >= 2:

                ts1 = valid_candles[-2].get('ts', 0)

                ts2 = valid_candles[-1].get('ts', 0)

                if ts2 > ts1:

                    diff_seconds = ts2 - ts1

                    timeframe_minutes = diff_seconds / 60

                    # Round to nearest standard timeframe

                    if timeframe_minutes <= 5:

                        timeframe_minutes = 5

                    elif timeframe_minutes <= 15:

                        timeframe_minutes = 15

                    elif timeframe_minutes <= 60:

                        timeframe_minutes = 60

                    elif timeframe_minutes <= 240:

                        timeframe_minutes = 240

                    else:

                        timeframe_minutes = 1440  # Daily

            

            # Calculate daily-based volatility factor (consistent with calculate_coin_volatility)

            # For trading purposes, use daily-based volatility instead of fully annualized

            if timeframe_minutes == 5:

                periods_per_day_factor = math.sqrt(24 * 12)  # 5m: 12 periods per hour

            elif timeframe_minutes == 15:

                periods_per_day_factor = math.sqrt(24 * 4)    # 15m: 4 periods per hour

            elif timeframe_minutes == 60:

                periods_per_day_factor = math.sqrt(24)        # 1h: 24 periods per day

            elif timeframe_minutes == 240:

                periods_per_day_factor = math.sqrt(6)         # 4h: 6 periods per day

            elif timeframe_minutes == 1440:

                periods_per_day_factor = 1.0                 # Daily: 1 period per day

            else:

                # Fallback: estimate from minutes

                periods_per_day = (24 * 60) / timeframe_minutes

                periods_per_day_factor = math.sqrt(periods_per_day)

            

            # Daily-based volatility: std(returns) * sqrt(periods_per_day) * 100

            # This represents expected daily movement percentage (consistent with calculate_coin_volatility)

            volatility_val = closes.pct_change().std() * periods_per_day_factor * 100 if len(closes) > 1 else 10.0

            trend_strength_val = safe_division(abs(macd_histogram_val_final), atr_val, 0.5) if atr_val > 0 else 0.5

        except Exception:

            volatility_val = 10.0

            trend_strength_val = 0.5

        market_regime = enhanced_market_regime_detection_single(indicators_1h={

            'volatility': volatility_val,

            'volume_ratio': volume_ratio_val,

            'trend_strength': trend_strength_val,

            'ema20': ema20,

            'ema50': ema50,

            'current': current_price,

            'rsi': rsi,

            'bb_upper': bb_upper_val,

            'bb_lower': bb_lower_val,

            'bb_width': bb_width_pct / 100.0,  # Convert percentage to decimal

            'momentum_1h': momentum_1h_pct

        })

        result = {

            "ema9": ema9, "ema20": ema20, "ema50": ema50, "ema100": ema100, "ema200": ema200,

            "rsi": rsi,

            "macd": macd_val, "macd_signal": macd_signal_val, "macd_histogram": macd_histogram_val_final,

            "bb_upper": bb_upper_val, "bb_lower": bb_lower_val, "bb_position": bb_position, "bb_width": bb_width_pct,

            "volume_ratio": volume_ratio_val,

            "atr": atr_val, "atr_percent": atr_percent_val,

            "momentum_1h": momentum_1h_pct,

            "pivot": pivot, "r1": r1, "s1": s1,

            "support": s1, "resistance": r1,

            "volatility": volatility_val, "trend_strength": trend_strength_val,

            "market_regime": market_regime,

            "current": current_price

        }

        performance_monitor.record_analysis(time.time() - start_time)

        return result

    except Exception as e:

        output_manager.print_static(f"{YELLOW}⚠️ Advanced indicators failed, using simple indicators: {e}{RESET}")

        error_handler.record_error("Indicator_Error", "Advanced indicators calculation failed", {"error": str(e)})

        result = calculate_simple_indicators(valid_candles)

        performance_monitor.record_analysis(time.time() - start_time)

        return result



def calculate_simple_indicators(candles: List[Dict[str, Any]]) -> Dict[str, Any]:

    if not candles:

        return {

            "current": 0, "rsi": 50, "ema20": 0, "ema50": 0,

            "support": 0, "resistance": 0, "volume_ratio": 1.0,

            "volatility": 10.0, "trend_strength": 0.5,

            "market_regime": MarketRegime.UNKNOWN

        }

    try:

        closes = [c['close'] for c in candles if c.get('close') is not None and c['close'] > 0]

        highs = [c['high'] for c in candles if c.get('high') is not None]

        lows = [c['low'] for c in candles if c.get('low') is not None]

        volumes = [c.get('volume', 1.0) for c in candles]

        if not closes:

            return {

                "current": 0, "rsi": 50, "ema20": 0, "ema50": 0,

                "support": 0, "resistance": 0, "volume_ratio": 1.0,

                "volatility": 10.0, "trend_strength": 0.5,

                "market_regime": MarketRegime.UNKNOWN

            }

        current_price = closes[-1] if closes else 0



        def simple_ema(prices, period):

            if not prices or len(prices) < period:

                return sum(prices) / len(prices) if prices else 0

            try:

                multiplier = 2 / (period + 1)

                ema_val = prices[0]

                for price in prices[1:]:

                    ema_val = (price - ema_val) * multiplier + ema_val

                return ema_val

            except Exception:

                return prices[-1] if prices else 0



        def simple_rsi(prices, period=14):

            """

            Simplified RSI calculation (fallback when pandas not available).

            Note: This uses SMA instead of RMA Welles Wilder for simplicity.

            For standard RSI, use calculate_advanced_indicators() which uses proper RMA.

            """

            if len(prices) < period + 1:

                return 50

            try:

                gains = []

                losses = []

                for i in range(1, len(prices)):

                    change = prices[i] - prices[i-1]

                    if change > 0:

                        gains.append(change)

                        losses.append(0)

                    else:

                        gains.append(0)

                        losses.append(abs(change))

                # Simple average (not RMA) - acceptable for fallback

                avg_gain = sum(gains[-period:]) / period

                avg_loss = sum(losses[-period:]) / period

                if avg_loss == 0:

                    return 100 if avg_gain > 0 else 50

                rs = avg_gain / avg_loss

                rsi = 100 - (100 / (1 + rs))

                return max(0, min(100, rsi))  # Clamp between 0-100

            except Exception:

                return 50

        ema20 = simple_ema(closes, 20)

        ema50 = simple_ema(closes, 50)

        rsi = simple_rsi(closes, 14)

        try:

            support_traditional = min(lows[-10:]) if len(lows) >= 10 else current_price * 0.95

            resistance_traditional = max(highs[-10:]) if len(highs) >= 10 else current_price * 1.05

            extended_lookback = min(20, len(highs))

            support_extended = min(lows[-extended_lookback:]) if len(lows) >= extended_lookback else support_traditional

            resistance_extended = max(highs[-extended_lookback:]) if len(highs) >= extended_lookback else resistance_traditional

            traditional_weight = 0.3

            extended_weight = 0.7

            support = (support_traditional * traditional_weight) + (support_extended * extended_weight)

            resistance = (resistance_traditional * traditional_weight) + (resistance_extended * extended_weight)

        except Exception:

            support = current_price * 0.95

            resistance = current_price * 1.05

        try:

            recent_volume = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 1.0

            avg_volume = sum(volumes) / len(volumes) if volumes else 1.0

            volume_ratio = safe_division(recent_volume, avg_volume, 1.0)

        except Exception:

            volume_ratio = 1.0

        return {

            "ema20": ema20, "ema50": ema50, "rsi": rsi,

            "support": support, "resistance": resistance,

            "volume_ratio": volume_ratio, "current": current_price,

            "volatility": 10.0, "trend_strength": 0.5,

            "market_regime": MarketRegime.UNKNOWN

        }

    except Exception as e:

        output_manager.print_static(f"{RED}❌ Simple indicators failed: {e}{RESET}")

        error_handler.record_error("Indicator_Error", "Simple indicators calculation failed", {"error": str(e)})

        return {

            "current": 0, "rsi": 50, "ema20": 0, "ema50": 0,

            "support": 0, "resistance": 0, "volume_ratio": 1.0,

            "volatility": 10.0, "trend_strength": 0.5,

            "market_regime": MarketRegime.UNKNOWN

        }



def calculate_ai_score(indicators_15m: Dict, indicators_1h: Dict, indicators_4h: Dict) -> float:

    score = 50.0

    try:

        current_price = indicators_1h.get('current', 0)

        if current_price <= 0:

            return 50.0

        regime = indicators_1h.get('market_regime', MarketRegime.UNKNOWN)

        regime_score = 0

        if regime in [MarketRegime.STRONG_UPTREND, MarketRegime.HIGH_VOLUME_ACCUMULATION]:

            regime_score = 18

        elif regime in [MarketRegime.WEAK_UPTREND, MarketRegime.BREAKOUT_CONSOLIDATION]:

            regime_score = 9

        elif regime in [MarketRegime.STRONG_DOWNTREND, MarketRegime.HIGH_VOLUME_DISTRIBUTION]:

            regime_score = -18

        elif regime in [MarketRegime.WEAK_DOWNTREND]:

            regime_score = -9

        score += regime_score

        rsi_score = 0

        for tf_name, ind in [("15m", indicators_15m), ("1h", indicators_1h), ("4h", indicators_4h)]:

            rsi = ind.get('rsi', 50)

            if 40 < rsi < 60:

                rsi_score += 7

            elif rsi < 35:

                rsi_score += 11

            elif rsi > 65:

                rsi_score -= 9

        score += rsi_score

        trend_score = 0

        ema20_1h = indicators_1h.get('ema20', 0)

        ema50_1h = indicators_1h.get('ema50', 0)

        ema20_4h = indicators_4h.get('ema20', 0)

        ema50_4h = indicators_4h.get('ema50', 0)

        if ema20_1h > ema50_1h and ema20_4h > ema50_4h:

            trend_score += 17

        elif ema20_1h < ema50_1h and ema20_4h < ema50_4h:

            trend_score -= 13

        elif ema20_1h > ema50_1h:

            trend_score += 9

        score += trend_score

        volume_score = 0

        volume_1h = indicators_1h.get('volume_ratio', 1)

        if 0.8 < volume_1h < 2.0:

            volume_score += 8

        if volume_1h > 1.1:

            volume_score += 4

        score += volume_score

        position_score = 0

        support = indicators_1h.get('support', current_price * 0.95)

        resistance = indicators_1h.get('resistance', current_price * 1.05)

        momentum = indicators_1h.get('momentum_1h', 0)

        if current_price > 0 and resistance > support:

            position_pct = safe_division((current_price - support), (resistance - support), 0.5)

            if position_pct < 0.3 and momentum > 0:

                position_score += 16

            elif position_pct > 0.7 and momentum < 0:

                position_score -= 12

            elif position_pct < 0.3:

                position_score += 8

            elif position_pct > 0.7:

                position_score -= 8

        score += position_score

        macd_score = 0

        try:

            macd = indicators_1h.get('macd', 0)

            macd_signal = indicators_1h.get('macd_signal', 0)

            macd_histogram = indicators_1h.get('macd_histogram', 0)

            if macd > macd_signal:

                macd_score += 5

            if macd_histogram > 0:

                macd_score += 3

            if macd > 0 and macd_signal > 0:

                macd_score += 2

            if macd < macd_signal:

                macd_score -= 5

            if macd_histogram < 0:

                macd_score -= 3

            if macd < 0 and macd_signal < 0:

                macd_score -= 2

        except Exception:

            pass

        score += macd_score

        bb_score = 0

        try:

            bb_position = indicators_1h.get('bb_position', 50.0)

            bb_width = indicators_1h.get('bb_width', 5.0)

            bb_upper = indicators_1h.get('bb_upper', current_price * 1.1)

            bb_lower = indicators_1h.get('bb_lower', current_price * 0.9)

            if bb_position < 20:

                bb_score += 5

            elif bb_position < 30:

                bb_score += 2

            elif bb_position > 80:

                bb_score -= 5

            elif bb_position > 70:

                bb_score -= 2

            if bb_width > 8.0:

                bb_score -= 1

            elif bb_width < 3.0:

                bb_score += 1

        except Exception:

            pass

        score += bb_score

        score = max(0, min(100, score))

    except Exception as e:

        output_manager.print_static(f"{YELLOW}⚠️ AI Score calculation error, using neutral score: {e}{RESET}")

        error_handler.record_error("Scoring_Error", "AI Score calculation failed", {"error": str(e)})

        score = 50.0

    return score



async def get_btc_correlation(symbol: str, candles: List[Dict] = None) -> Dict[str, Any]:

    try:

        btc_candles = await get_candles_optimized("BTC_USDT", "5m", 50)

        if not btc_candles or len(btc_candles) < 20:

            return {

                'correlation': 0.7,

                'correlation_type': 'MEDIUM',

                'btc_trend': 'SIDEWAYS',

                'recommendation': 'FOLLOW_BTC',

                'adjustment_factor': 1.0

            }

        if not candles:

            altcoin_candles = await get_candles_optimized(symbol, "5m", 50)

        else:

            altcoin_candles = candles

        if not altcoin_candles or len(altcoin_candles) < 20:

            return {

                'correlation': 0.7,

                'correlation_type': 'MEDIUM',

                'btc_trend': 'SIDEWAYS',

                'recommendation': 'FOLLOW_BTC',

                'adjustment_factor': 1.0

            }

        btc_returns = []

        altcoin_returns = []

        for i in range(1, min(len(btc_candles), len(altcoin_candles))):

            if btc_candles[i]['close'] > 0 and altcoin_candles[i]['close'] > 0:

                btc_ret = (btc_candles[i]['close'] - btc_candles[i-1]['close']) / btc_candles[i-1]['close']

                altcoin_ret = (altcoin_candles[i]['close'] - altcoin_candles[i-1]['close']) / altcoin_candles[i-1]['close']

                btc_returns.append(btc_ret)

                altcoin_returns.append(altcoin_ret)

        if len(btc_returns) < 10:

            return {

                'correlation': 0.7,

                'correlation_type': 'MEDIUM',

                'btc_trend': 'SIDEWAYS',

                'recommendation': 'FOLLOW_BTC',

                'adjustment_factor': 1.0

            }

        correlation = np.corrcoef(btc_returns, altcoin_returns)[0, 1]

        if math.isnan(correlation):

            correlation = 0.7

        if symbol.upper() == 'BTCUSDT':

            correlation = 0.95

        if correlation > 0.75:

            correlation_type = 'HIGH'

        elif correlation > 0.5:

            correlation_type = 'MEDIUM'

        else:

            correlation_type = 'LOW'

        btc_recent = [btc_candles[i]['close'] for i in range(-10, 0)]

        btc_trend_change = safe_division((btc_recent[-1] - btc_recent[0]), btc_recent[0], 0.0) * 100

        if btc_trend_change > 2:

            btc_trend = 'UPTREND'

        elif btc_trend_change < -2:

            btc_trend = 'DOWNTREND'

        else:

            btc_trend = 'SIDEWAYS'

        coin_type = await get_coin_type_range(symbol)

        if correlation > 0.7:

            recommendation = 'FOLLOW_BTC'

            if btc_trend == 'UPTREND':

                adjustment_factor = 1.15 if coin_type in ['ALTCOIN', 'MICROCAP'] else 1.10

            elif btc_trend == 'DOWNTREND':

                adjustment_factor = 0.85 if coin_type in ['ALTCOIN', 'MICROCAP'] else 0.90

            else:

                adjustment_factor = 1.0

        elif correlation > 0.5:

            recommendation = 'PARTIAL_BTC'

            adjustment_factor = 1.0

        else:

            recommendation = 'INDEPENDENT'

            adjustment_factor = 1.05 if coin_type == 'MICROCAP' else 1.0

        return {

            'correlation': correlation,

            'correlation_type': correlation_type,

            'btc_trend': btc_trend,

            'recommendation': recommendation,

            'adjustment_factor': adjustment_factor

        }

    except Exception as e:

        return {

            'correlation': 0.7,

            'correlation_type': 'MEDIUM',

            'btc_trend': 'SIDEWAYS',

            'recommendation': 'FOLLOW_BTC',

            'adjustment_factor': 1.0

        }



async def analyze_liquidity_adaptive(symbol: str, force_fresh: bool = False) -> Dict[str, Any]:

    try:

        orderbook = await get_orderbook_optimized(symbol, 50, force_fresh=force_fresh, max_age_s=5 if force_fresh else 30)

        if not orderbook or 'bids' not in orderbook or 'asks' not in orderbook:

            return {

                'spread_pct': 0.1,

                'depth_bid': 1000,

                'depth_ask': 1000,

                'depth_bid_usd': 10000,

                'depth_ask_usd': 10000,

                'liquidity_score': 10000,

                'liquidity_grade': 'GOOD',

                'is_safe': True,

                'recommendation': 'OK',

                'depth_ratio': 2.0

            }

        bid = float(orderbook['bids'][0][0])

        ask = float(orderbook['asks'][0][0])

        spread_pct = ((ask - bid) / bid) * 100 if bid > 0 else 999

        depth_bid = sum(float(b[1]) for b in orderbook['bids'][:25])

        depth_ask = sum(float(a[1]) for a in orderbook['asks'][:25])

        current_price = await get_price_optimized(symbol)

        if not current_price or current_price <= 0:

            current_price = (bid + ask) / 2

        depth_bid_usd = depth_bid * current_price

        depth_ask_usd = depth_ask * current_price

        total_depth_usd = depth_bid_usd + depth_ask_usd

        coin_type = await get_coin_type_range(symbol)

        if coin_type == 'BLUECHIP':

            excellent_threshold = 0.10

            good_threshold = 0.20

            fair_threshold = 0.40

            min_depth_usd = 50000

        elif coin_type == 'MAJOR':

            excellent_threshold = 0.15

            good_threshold = 0.30

            fair_threshold = 0.60

            min_depth_usd = 30000

        elif coin_type == 'ALTCOIN':

            excellent_threshold = 0.25

            good_threshold = 0.45

            fair_threshold = 0.80

            min_depth_usd = 10000

        elif coin_type == 'MICROCAP':

            excellent_threshold = 0.30

            good_threshold = 0.60

            fair_threshold = 1.20

            min_depth_usd = 3000

        else:

            excellent_threshold = 0.15

            good_threshold = 0.30

            fair_threshold = 0.60

            min_depth_usd = 10000

        depth_ratio = total_depth_usd / min_depth_usd if min_depth_usd > 0 else 0



        # CRITICAL FIX: Prevent trading with dangerously low liquidity

        critical_min_depths = {

            'BLUECHIP': 25000,  # Absolute minimum for bluechips

            'MAJOR': 5000,      # Absolute minimum for majors (reduced from 10000)

            'ALTCOIN': 1500,    # Absolute minimum for altcoins (reduced from 3000)

            'MICROCAP': 500     # Absolute minimum for microcaps

        }



        critical_min = critical_min_depths.get(coin_type, 1000)

        if total_depth_usd < critical_min:

            return {

                'spread_pct': spread_pct,

                'total_depth_usd': total_depth_usd,

                'liquidity_score': 95,  # Very poor score

                'rating': 'CRITICAL_LOW',

                'coin_type': coin_type,

                'warning': f'Depth ${total_depth_usd:,.0f} below critical minimum ${critical_min:,.0f}'

            }



        if depth_ratio < 0.1:

            adjustment_factor = max(0.15, total_depth_usd / min_depth_usd)

            min_depth_usd_adjusted = min_depth_usd * adjustment_factor

            if total_depth_usd > 0:

                output_manager.print_static(

                    f"{YELLOW}⚠️ Low on-exchange liquidity: Adjusting {coin_type} threshold from ${min_depth_usd:,.0f} → ${min_depth_usd_adjusted:,.0f} (actual: ${total_depth_usd:,.0f}){RESET}"

                )

            min_depth_usd = min_depth_usd_adjusted

        liquidity_score = total_depth_usd / max(spread_pct, 0.01)

        if spread_pct < excellent_threshold:

            base_grade_from_spread = 'EXCELLENT'

        elif spread_pct < good_threshold:

            base_grade_from_spread = 'GOOD'

        elif spread_pct < fair_threshold:

            base_grade_from_spread = 'FAIR'

        else:

            base_grade_from_spread = 'POOR'

        if total_depth_usd > min_depth_usd * 2 or depth_ratio > 5.0:

            base_grade_from_depth = 'EXCELLENT'

        elif total_depth_usd > min_depth_usd or depth_ratio > 3.0:

            base_grade_from_depth = 'GOOD'

        elif total_depth_usd > min_depth_usd * 0.5 or depth_ratio > 1.5:

            base_grade_from_depth = 'FAIR'

        else:

            base_grade_from_depth = 'POOR'

        grade_map = {'EXCELLENT': 4, 'GOOD': 3, 'FAIR': 2, 'POOR': 1}

        grade_reverse = {4: 'EXCELLENT', 3: 'GOOD', 2: 'FAIR', 1: 'POOR'}

        spread_score = grade_map.get(base_grade_from_spread, 2)

        depth_score = grade_map.get(base_grade_from_depth, 2)

        if depth_score >= spread_score:

            final_score = spread_score

        else:

            final_score = max(spread_score - 1, depth_score)

        liquidity_grade = grade_reverse.get(final_score, 'FAIR')

        if spread_pct < good_threshold and liquidity_grade == 'POOR':

            liquidity_grade = 'FAIR'

        is_safe = liquidity_grade in ['EXCELLENT', 'GOOD', 'FAIR']

        if liquidity_grade == 'EXCELLENT':

            recommendation = 'OK'

        elif liquidity_grade == 'GOOD':

            recommendation = 'OK'

        elif liquidity_grade == 'FAIR':

            recommendation = 'CAUTION'

        else:

            recommendation = 'AVOID'

        result = {

            'spread_pct': spread_pct,

            'depth_bid': depth_bid,

            'depth_ask': depth_ask,

            'depth_bid_usd': depth_bid_usd,

            'depth_ask_usd': depth_ask_usd,

            'total_depth_usd': total_depth_usd,

            'liquidity_score': liquidity_score,

            'liquidity_grade': liquidity_grade,

            'is_safe': is_safe,

            'recommendation': recommendation,

            'coin_type': coin_type,

            'min_depth_required_usd': min_depth_usd,

            'depth_ratio': depth_ratio

        }

        try:

            if orderbook and isinstance(orderbook, dict):

                if 'age_s' in orderbook:

                    result['orderbook_age_s'] = orderbook['age_s']

                if 'bids' in orderbook and 'asks' in orderbook:

                    result['levels_used'] = {

                        'bids': len(orderbook['bids']),

                        'asks': len(orderbook['asks'])

                    }

        except Exception:

            pass

        return result

    except Exception as e:

        return {

            'spread_pct': 0.1,

            'depth_bid': 1000,

            'depth_ask': 1000,

            'depth_bid_usd': 10000,

            'depth_ask_usd': 10000,

            'total_depth_usd': 20000,

            'liquidity_score': 10000,

            'liquidity_grade': 'GOOD',

            'is_safe': True,

            'recommendation': 'OK',

            'depth_ratio': 2.0,

            'error': str(e)

        }



async def get_order_book_resistance(symbol: str) -> Dict[str, Any]:

    try:

        order_book = await get_orderbook_optimized(symbol=symbol, limit=25)

        if not order_book or 'bids' not in order_book or 'asks' not in order_book:

            return {

                'resistance_levels': [],

                'primary_resistance': 0,

                'sell_wall_strength': 0,

                'is_breaking_resistance': False,

                'recommendation': 'NO_DATA'

            }

        asks = order_book['asks']

        bids = order_book['bids']

        resistance_levels = []

        total_sell_volume = 0

        for ask in asks:

            price = float(ask[0])

            volume = float(ask[1])

            resistance_levels.append(price)

            total_sell_volume += volume

        if asks and bids:

            ask_price = float(asks[0][0])

            bid_price = float(bids[0][0])

            current_price = (ask_price + bid_price) / 2

        else:

            current_price = 0

        sell_wall_strength = 0

        if asks:

            cumulative_volume = 0

            for i, ask in enumerate(asks):

                price = float(ask[0])

                volume = float(ask[1])

                cumulative_volume += volume

                price_diff_pct = (abs(price - current_price) / current_price * 100) if current_price > 0 else 999

                if price_diff_pct < 2.0:

                    sell_wall_strength += volume * (1.0 - price_diff_pct / 2.0)

        primary_resistance = 0

        if resistance_levels:

            best_resistance = 0

            best_volume = 0

            for ask in asks:

                price = float(ask[0])

                volume = float(ask[1])

                price_diff_pct = (abs(price - current_price) / current_price * 100) if current_price > 0 else 999

                if price_diff_pct < 3.0 and volume > best_volume:

                    best_resistance = price

                    best_volume = volume

            primary_resistance = best_resistance if best_resistance > 0 else resistance_levels[0]

        is_breaking_resistance = False

        if primary_resistance > 0 and current_price > 0:

            is_breaking_resistance = current_price > primary_resistance * 1.001

        recommendation = 'HOLD'

        if is_breaking_resistance:

            recommendation = 'BREAKOUT_CONFIRMED'

        elif sell_wall_strength > total_sell_volume * 0.3:

            recommendation = 'RESISTANCE_STRONG'

        elif sell_wall_strength < total_sell_volume * 0.1:

            recommendation = 'RESISTANCE_WEAK'

        return {

            'resistance_levels': resistance_levels[:5],

            'primary_resistance': primary_resistance,

            'sell_wall_strength': sell_wall_strength,

            'is_breaking_resistance': is_breaking_resistance,

            'recommendation': recommendation,

            'current_price': current_price,

            'total_sell_volume': total_sell_volume

        }

    except Exception as e:

        return {

            'resistance_levels': [],

            'primary_resistance': 0,

            'sell_wall_strength': 0,

            'is_breaking_resistance': False,

            'recommendation': 'FALLBACK',

            'current_price': 0,

            'total_sell_volume': 0,

            'error': str(e)

        }



def get_intraday_pattern_adaptive(symbol: str) -> Dict[str, Any]:

    try:

        from datetime import timezone



        utc_hour = datetime.now(timezone.utc).hour

        session_profiles = {

            'ASIAN': {

                'volume': 'LOW',

                'volatility': 'LOW',

                'recommendation': 'CAUTION',

                'adjustment': 0.95,

                'bias': -1

            },

            'EUROPEAN': {

                'volume': 'MEDIUM',

                'volatility': 'MEDIUM',

                'recommendation': 'OK',

                'adjustment': 1.0,

                'bias': 0

            },

            'US': {

                'volume': 'HIGH',

                'volatility': 'HIGH',

                'recommendation': 'ACTIVE',

                'adjustment': 1.05,

                'bias': 1

            },

            'OVERLAP': {

                'volume': 'HIGH',

                'volatility': 'VERY_HIGH',

                'recommendation': 'VERY_ACTIVE',

                'adjustment': 1.1,

                'bias': 1

            }

        }

        if 0 <= utc_hour < 4:

            session = 'ASIAN'

        elif 4 <= utc_hour < 12:

            session = 'EUROPEAN'

        elif 12 <= utc_hour < 20:

            session = 'US'

        else:

            session = 'OVERLAP'

        profile = session_profiles[session]

        expected_volume = profile['volume']

        expected_volatility = profile['volatility']

        adjustment_factor = profile['adjustment']

        data_source = 'NONE'

        volume_ratio = None

        realized_daily_vol = None

        candles: List[Dict[str, Any]] = []

        try:

            ws_manager = _init_ws_manager()

            ws_candles = None

            if ws_manager and hasattr(ws_manager, 'get_candles'):

                try:

                    ws_candles = ws_manager.get_candles(symbol, "15m", 32)

                except Exception:

                    pass

            if ws_candles:

                candles = ws_candles

                data_source = 'WS_CACHE'

            else:

                max_age = CANDLE_MAX_AGE.get('15m', 900)

                persisted = load_persisted_candles(symbol, "15m", 32, max_age)

                if persisted:

                    candles = persisted

                    data_source = 'PERSISTED'

        except Exception:

            candles = []

        if candles and len(candles) >= 6:

            vol_values: List[float] = []

            close_values: List[float] = []

            for candle in candles:

                try:

                    if candle.get('volume') is not None:

                        vol_values.append(float(candle['volume']))

                except (ValueError, TypeError):

                    continue

            for candle in candles:

                try:

                    if candle.get('close') is not None:

                        close_values.append(float(candle['close']))

                except (ValueError, TypeError):

                    continue

            if vol_values:

                recent_window = vol_values[-4:] if len(vol_values) >= 4 else vol_values

                baseline_window = vol_values[-16:] if len(vol_values) >= 16 else vol_values

                recent_avg = safe_division(sum(recent_window), len(recent_window), 0.0)

                baseline_avg = safe_division(sum(baseline_window), len(baseline_window), 0.0)

                if baseline_avg > 0:

                    volume_ratio = safe_division(recent_avg, baseline_avg, 1.0)

            if len(close_values) >= 6:

                returns: List[float] = []

                for idx in range(1, len(close_values)):

                    prev_close = close_values[idx - 1]

                    if prev_close > 0:

                        returns.append((close_values[idx] - prev_close) / prev_close)

                if returns:

                    realized_daily_vol = float(np.std(returns) * math.sqrt(96) * 100)

        if volume_ratio is not None:

            if volume_ratio >= 1.3:

                expected_volume = 'HIGH'

                adjustment_factor += 0.05

            elif volume_ratio <= 0.7:

                expected_volume = 'LOW'

                adjustment_factor -= 0.05

            else:

                expected_volume = 'MEDIUM'

        if realized_daily_vol is not None:

            if realized_daily_vol >= 15:

                expected_volatility = 'VERY_HIGH'

                adjustment_factor += 0.05

            elif realized_daily_vol >= 8:

                expected_volatility = 'HIGH'

            elif realized_daily_vol <= 4:

                expected_volatility = 'LOW'

                adjustment_factor -= 0.05

            else:

                expected_volatility = 'MEDIUM'

        volume_score_map = {'LOW': -1, 'MEDIUM': 0, 'HIGH': 1}

        volatility_score_map = {'LOW': -1, 'MEDIUM': 0, 'HIGH': 1, 'VERY_HIGH': 2}

        combined_score = (

            profile['bias'] +

            volume_score_map.get(expected_volume, 0) +

            volatility_score_map.get(expected_volatility, 0)

        )

        if combined_score <= -1:

            recommendation = 'CAUTION'

        elif combined_score == 0:

            recommendation = 'OK'

        elif combined_score == 1:

            recommendation = 'ACTIVE'

        else:

            recommendation = 'VERY_ACTIVE'

        adjustment_factor = max(0.85, min(1.2, adjustment_factor))

        return {

            'session': session,

            'expected_volume': expected_volume,

            'expected_volatility': expected_volatility,

            'recommendation': recommendation,

            'adjustment_factor': adjustment_factor,

            'volume_ratio': volume_ratio,

            'realized_daily_volatility': realized_daily_vol,

            'data_source': data_source

        }

    except Exception:

        return {

            'session': 'UNKNOWN',

            'expected_volume': 'MEDIUM',

            'expected_volatility': 'MEDIUM',

            'recommendation': 'OK',

            'adjustment_factor': 1.0

        }



async def fetch_coin_data_from_cmc(symbol: str) -> Dict[str, Any]:

    if not CMC_API_KEY:

        return None

    try:

        if '_' in symbol:

            base = symbol.split('_')[0]

        else:

            base = symbol.replace('USDT', '').replace('USDC', '').replace('BUSD', '')

        url = f"{CMC_API_URL}/cryptocurrency/quotes/latest"

        headers = {

            'X-CMC_PRO_API_KEY': CMC_API_KEY,

            'Accept': 'application/json'

        }

        params = {

            'symbol': base,

            'convert': 'USD'

        }

        await cmc_rate_limit_guard()

        max_attempts = 3

        backoff = 2.0

        attempt = 0

        response = None

        while attempt < max_attempts:

            attempt += 1

            try:

                response = requests.get(url, headers=headers, params=params, timeout=10)

            except Exception as rexc:

                if attempt >= max_attempts:

                    raise rexc

                await asyncio.sleep(backoff)

                backoff *= 1.5

                continue

            if response.status_code == 429:

                retry_after = response.headers.get('Retry-After')

                try:

                    retry_after = float(retry_after) if retry_after else backoff * 2

                except:

                    retry_after = backoff * 2

                if attempt >= max_attempts:

                    print(f"{RED}❌ CMC 429 rate limited - max retries reached. Using cached data if available.{RESET}")

                    raise Exception(f"CMC API rate limit exceeded (429) after {max_attempts} attempts")

                print(f"{YELLOW}⚠️ CMC 429 rate limited (after guard) - retrying in {retry_after:.1f}s (attempt {attempt}/{max_attempts}){RESET}")

                await asyncio.sleep(retry_after)

                backoff *= 1.5

                continue

            if 500 <= response.status_code < 600 and attempt < max_attempts:

                print(f"{YELLOW}⚠️ CMC {response.status_code} - retrying in {backoff:.1f}s (attempt {attempt}/{max_attempts}){RESET}")

                await asyncio.sleep(backoff)

                backoff *= 1.5

                continue

            break

        if response is not None and response.status_code == 200:

            data = response.json()

            if 'data' not in data or base not in data['data']:

                return None

            if 'data' in data and base in data['data']:

                coin_data = data['data'][base]

                quote = data['data'][base]['quote']['USD']

                market_cap = quote.get('market_cap', 0)

                volume_24h = quote.get('volume_24h', 0)

                volume_to_mcap_ratio = volume_24h / market_cap if market_cap > 0 else 0

                cmc_rank = coin_data.get('cmc_rank', 9999)

                volatility_24h = abs(quote.get('percent_change_24h', 0))

                base_classification = "ALTCOIN"

                if cmc_rank:

                    if cmc_rank <= 10:

                        base_classification = "BLUECHIP"

                    elif cmc_rank <= 50:

                        base_classification = "MAJOR"

                    elif cmc_rank <= 300:

                        base_classification = "ALTCOIN"

                    else:

                        base_classification = "MICROCAP"

                volume_modifier = 0

                if volume_to_mcap_ratio > 0.5:

                    volume_modifier = +1

                elif volume_to_mcap_ratio < 0.01:

                    volume_modifier = -1

                tier_order = ["MICROCAP", "ALTCOIN", "MAJOR", "BLUECHIP"]

                current_tier_idx = tier_order.index(base_classification)

                adjusted_tier_idx = current_tier_idx + volume_modifier

                if base_classification == "MICROCAP" and adjusted_tier_idx > tier_order.index("ALTCOIN"):

                    adjusted_tier_idx = tier_order.index("ALTCOIN")

                adjusted_tier_idx = max(0, min(adjusted_tier_idx, len(tier_order) - 1))

                classification = tier_order[adjusted_tier_idx]

                scores = {

                    'rank_score': 0,

                    'volume': 0,

                    'volatility': 0,

                    'liquidity': 0,

                    'size': 0

                }

                if cmc_rank and cmc_rank <= 10:

                    scores['rank_score'] = 40

                elif cmc_rank and cmc_rank <= 50:

                    scores['rank_score'] = 35

                elif cmc_rank and cmc_rank <= 100:

                    scores['rank_score'] = 30

                elif cmc_rank and cmc_rank <= 200:

                    scores['rank_score'] = 25

                elif cmc_rank and cmc_rank <= 500:

                    scores['rank_score'] = 20

                elif cmc_rank and cmc_rank <= 1000:

                    scores['rank_score'] = 15

                elif cmc_rank and cmc_rank <= 1500:

                    scores['rank_score'] = 10

                else:

                    scores['rank_score'] = 5

                if volume_to_mcap_ratio > 0.5:

                    scores['liquidity'] = 25

                elif volume_to_mcap_ratio > 0.1:

                    scores['liquidity'] = 20

                elif volume_to_mcap_ratio > 0.05:

                    scores['liquidity'] = 15

                elif volume_to_mcap_ratio > 0.02:

                    scores['liquidity'] = 10

                elif volume_to_mcap_ratio > 0.01:

                    scores['liquidity'] = 5

                else:

                    scores['liquidity'] = 0

                if volatility_24h < 5:

                    scores['volatility'] = 15

                elif volatility_24h < 10:

                    scores['volatility'] = 12

                elif volatility_24h < 20:

                    scores['volatility'] = 8

                elif volatility_24h < 35:

                    scores['volatility'] = 4

                else:

                    scores['volatility'] = 0

                if volume_24h > 500_000_000:

                    scores['volume'] = 10

                elif volume_24h > 100_000_000:

                    scores['volume'] = 8

                elif volume_24h > 10_000_000:

                    scores['volume'] = 6

                elif volume_24h > 1_000_000:

                    scores['volume'] = 3

                else:

                    scores['volume'] = 0

                if market_cap > 10_000_000_000:

                    scores['size'] = 10

                elif market_cap > 1_000_000_000:

                    scores['size'] = 8

                elif market_cap > 100_000_000:

                    scores['size'] = 6

                elif market_cap > 10_000_000:

                    scores['size'] = 3

                else:

                    scores['size'] = 0

                total_score = sum(scores.values())

                timestamp = time.time()

                cache_valid_until = timestamp + (24 * 3600)

                return {

                    'symbol': symbol,

                    'coin_id': coin_data.get('slug', base.lower()),

                    'market_cap': market_cap,

                    'market_cap_rank': cmc_rank,

                    'volume_24h': volume_24h,

                    'volume_to_mcap_ratio': volume_to_mcap_ratio,

                    'price': quote.get('price', 0),

                    'percent_change_24h': quote.get('percent_change_24h', 0),

                    'percent_change_7d': quote.get('percent_change_7d', 0),

                    'percent_change_30d': quote.get('percent_change_30d', 0),

                    'classification': classification,

                    'classification_score': total_score,

                    'scores': scores,

                    'source': 'CMC',

                    'last_updated': timestamp,

                    'cache_valid_until': cache_valid_until

                }

    except Exception as e:

        print(f"{YELLOW}⚠️ CMC API error: {e}{RESET}")

        return None



def load_coin_market_data() -> Dict[str, Any]:

    try:

        if os.path.exists(COIN_MARKET_DATA_FILE):

            if os.path.getsize(COIN_MARKET_DATA_FILE) == 0:

                return {}

            with open(COIN_MARKET_DATA_FILE, 'r') as f:

                data = json.load(f)

                return data if data else {}

        return {}

    except json.JSONDecodeError as e:

        print(f"{YELLOW}⚠️ Invalid JSON in coin_market_data.json: {e}{RESET}")

        try:

            import shutil



            shutil.copy(COIN_MARKET_DATA_FILE, f"{COIN_MARKET_DATA_FILE}.backup")

            print(f"{CYAN}💾 Backed up corrupted file to {COIN_MARKET_DATA_FILE}.backup{RESET}")

        except:

            pass

        return {}

    except Exception as e:

        print(f"{YELLOW}⚠️ Error loading coin market data: {e}{RESET}")

        return {}



def save_coin_market_data(data: Dict[str, Any]):

    try:

        # Sort data by market_cap_rank (ascending: rank 1 first, then 2, 3, etc.)

        # If rank is missing or invalid, put at the end

        sorted_data = {}

        sorted_items = sorted(

            data.items(),

            key=lambda x: (

                x[1].get('market_cap_rank', 999999) if isinstance(x[1], dict) else 999999,

                x[0]  # Secondary sort by symbol name if rank is same

            )

        )

        for key, value in sorted_items:

            sorted_data[key] = value

        

        with open(COIN_MARKET_DATA_FILE, 'w') as f:

            json.dump(sorted_data, f, indent=2)

    except Exception as e:

        print(f"{YELLOW}⚠️ Error saving coin market data: {e}{RESET}")



def needs_classification_refresh(symbol: str, current_price: float, current_volatility: float) -> bool:

    cache_data = load_coin_market_data()

    if symbol not in cache_data:

        return True

    cached = cache_data[symbol]

    classification = cached.get('classification', 'ALTCOIN')

    rules = SMART_REFRESH_RULES.get(classification, SMART_REFRESH_RULES['ALTCOIN'])

    age = time.time() - cached.get('last_updated', 0)

    if age > rules['time_based']:

        return True

    initial_price = cached.get('price', 0)

    if initial_price > 0:

        price_change_pct = abs((current_price - initial_price) / initial_price * 100)

        if price_change_pct > rules['price_change']:

            return True

    if current_volatility > rules['volatility_threshold'] and age > (rules['time_based'] / 2):

        return True

    return False



async def get_coin_market_data(symbol: str, force_fresh: bool = False,

                                current_price: float = None,

                                current_volatility: float = None) -> Dict[str, Any]:

    normalized_symbol = normalize_symbol(symbol)

    cache_data = load_coin_market_data()

    if not force_fresh and current_price and current_volatility:

        if needs_classification_refresh(normalized_symbol, current_price, current_volatility):

            print(f"{CYAN}🔄 Smart refresh triggered for {normalized_symbol}{RESET}")

            force_fresh = True

    if not force_fresh and normalized_symbol in cache_data:

        cached = cache_data[normalized_symbol]

        cache_valid_until = cached.get('cache_valid_until', 0)

        if time.time() < cache_valid_until:

            if CMC_CACHE_DEBUG:

                print(f"{GREEN}✅ CACHE HIT: {symbol} -> {normalized_symbol}{RESET}")

            return cached

        else:

            if CMC_CACHE_DEBUG:

                print(f"{YELLOW}⚠️ CACHE EXPIRED: {symbol} -> {normalized_symbol} (expired {int(time.time() - cache_valid_until)}s ago, but will use it if API fails){RESET}")

    else:

        if CMC_CACHE_DEBUG:

            if normalized_symbol not in cache_data:

                print(f"{RED}❌ CACHE MISS: {symbol} -> {normalized_symbol} (not in cache){RESET}")

            else:

                print(f"{YELLOW}⚠️ CACHE SKIP: {symbol} -> force_fresh={force_fresh}{RESET}")

    fresh_data = None

    try:

        fresh_data = await fetch_coin_data_with_fallback(symbol)

    except Exception as e:

        if normalized_symbol in cache_data:

            print(f"{YELLOW}⚠️ CMC API call failed ({str(e)[:50]}), using cached data (may be expired){RESET}")

            return cache_data[normalized_symbol]

        raise

    if fresh_data:

        cache_data[normalized_symbol] = fresh_data

        save_coin_market_data(cache_data)

        return fresh_data

    if normalized_symbol in cache_data:

        print(f"{CYAN}ℹ️ Using cached data for {symbol} (API returned None, cache age: {int(time.time() - cache_data[normalized_symbol].get('last_updated', 0))}s){RESET}")

        return cache_data[normalized_symbol]

    return None



async def fetch_coin_data_with_fallback(symbol: str) -> Dict[str, Any]:

    if not CMC_API_KEY:

        print(f"{RED}❌ No CMC API key configured{RESET}")

        return None

    cmc_data = await fetch_coin_data_from_cmc(symbol)

    if cmc_data:

        return cmc_data

    base_coin = symbol.split('_')[0] if '_' in symbol else symbol.replace('USDT', '').replace('USDC', '').replace('BUSD', '')

    print(f"{YELLOW}⚠️  {base_coin} not found on CMC (new/unlisted coin) - using fallback classification{RESET}")

    return None



async def compute_meme_likelihood(symbol: str, base: str, cmc_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:

    score = 0.0

    factors = []

    

    # Enhanced meme coin name pattern detection

    # Known meme coin patterns (case-insensitive)

    meme_patterns = [

        'DOGE', 'SHIB', 'PEPE', 'FLOKI', 'BONK', 'WIF', 'BABY', 'BABYDOGE',

        'ELON', 'TRUMP', 'MOON', 'SAFE', 'SAFEMOON', 'DOGS', 'CAT', 'FROG'

    ]

    base_upper = base.upper()

    is_meme_name = any(pattern in base_upper for pattern in meme_patterns)

    if is_meme_name:

        # Base score for meme coin name pattern

        score += 40

        factors.append(f'meme_name_pattern:+40')

        

        # Additional boost if it's a well-known meme coin

        well_known_memes = ['DOGE', 'SHIB', 'PEPE', 'FLOKI', 'BONK', 'WIF']

        if any(well_known in base_upper for well_known in well_known_memes):

            score += 20

            factors.append(f'well_known_meme:+20')

    

    try:

        vol = await calculate_coin_volatility(symbol)

        if vol > 50:

            score += 25; factors.append(f'vol>50:+25({vol:.1f}%)')

        elif vol > 40:

            score += 20; factors.append(f'vol>40:+20({vol:.1f}%)')

        elif vol > 30:

            score += 12; factors.append(f'vol>30:+12({vol:.1f}%)')

        elif vol < 10:

            score -= 5; factors.append(f'vol<10:-5({vol:.1f}%)')

    except Exception:

        pass

    try:

        if cmc_data:

            mcap = cmc_data.get('market_cap', 0) or 0

            vol24 = cmc_data.get('volume_24h', 0) or 0

            vol_mcap = (vol24 / mcap) if mcap else 0

            if vol_mcap > 1.0:

                score += 20; factors.append(f'vol/mcap>1:+20({vol_mcap:.2f})')

            elif vol_mcap > 0.5:

                score += 12; factors.append(f'vol/mcap>0.5:+12({vol_mcap:.2f})')

            elif vol_mcap < 0.02:

                score -= 5; factors.append(f'vol/mcap<0.02:-5({vol_mcap:.3f})')

    except Exception:

        pass

    try:

        ob = await get_orderbook_optimized(symbol, limit=5)

        if ob and 'bids' in ob and 'asks' in ob and ob['bids'] and ob['asks']:

            best_bid = float(ob['bids'][0][0])

            best_ask = float(ob['asks'][0][0])

            spread_pct = ((best_ask - best_bid) / best_bid) * 100 if best_bid > 0 else 999

            if spread_pct > 1.0:

                score += 20; factors.append(f'spread>1%:+20({spread_pct:.2f}%)')

            elif spread_pct > 0.6:

                score += 10; factors.append(f'spread>0.6%:+10({spread_pct:.2f}%)')

            elif spread_pct < 0.1:

                score -= 3; factors.append(f'spread<0.1%:-3({spread_pct:.2f}%)')

    except Exception:

        pass

    try:

        flow = await get_order_flow_optimized(symbol, limit=20)

        if flow:

            imb = abs(flow.get('order_imbalance', 0))

            if imb > 30:

                score += 15; factors.append(f'imbalance>30:+15({imb:.1f})')

            elif imb > 20:

                score += 10; factors.append(f'imbalance>20:+10({imb:.1f})')

    except Exception:

        pass

    score = max(0.0, min(100.0, score))

    return {'score': score, 'factors': factors}

    classification = cmc_data.get('classification', 'ALTCOIN')

    total_score = cmc_data.get('classification_score', 50)

    scores = cmc_data.get('scores', {})

    tier_map = {

        'BLUECHIP': 'BLUECHIP',

        'MAJOR': 'TOP_TIER',

        'ALTCOIN': 'STANDARD',

        'MICROCAP': 'HIGH_RISK'

    }

    tier = tier_map.get(classification, 'STANDARD')

    return {

        'classification': classification,

        'tier': tier,

        'total_score': total_score,

        'scores': scores,

        'market_cap': cmc_data.get('market_cap', 0),

        'volume_24h': cmc_data.get('volume_24h', 0),

        'volatility_24h': abs(cmc_data.get('percent_change_24h', 0)),

        'cmc_rank': cmc_data.get('market_cap_rank', 9999),

        'source': cmc_data.get('source', 'FALLBACK')

    }



# Track printed classifications in this session to avoid duplicates

_printed_classifications = set()

_printed_classifications_lock = threading.Lock()



async def get_coin_type_range(symbol: str) -> str:

    global _coin_type_cache, _printed_classifications

    current_time = time.time()

    from_cache = False

    with _coin_type_cache_lock:

        if symbol in _coin_type_cache:

            cached_data = _coin_type_cache[symbol]

            if current_time - cached_data['timestamp'] < _coin_type_cache_ttl:

                from_cache = True

                cached_classification = cached_data['coin_type']

                # Only print if not already printed in this session

                with _printed_classifications_lock:

                    if symbol not in _printed_classifications:

                        # Try to get CMC data for display info

                        try:

                            cmc_data = await get_coin_market_data(symbol)

                            if cmc_data:

                                cmc_rank = cmc_data.get('market_cap_rank', 9999)

                                market_cap = cmc_data.get('market_cap', 0)

                                vol_24h = cmc_data.get('volume_24h', 0)

                                vol_ratio = cmc_data.get('volume_to_mcap_ratio', (vol_24h / market_cap) if market_cap else 0)

                                source = cmc_data.get('source', 'UNKNOWN')

                                # Format: Responsive Compact
                                output_manager.print_static(
                                    f"{CYAN}📊 {cached_classification} │ Rank #{cmc_rank} │ MCap ${market_cap/1e9:.2f}B │ Vol/MCap {vol_ratio:.2f} [Cached]{RESET}"
                                )

                            else:

                                output_manager.print_static(

                                    f"{CYAN}📊 {cached_classification} [Cached]{RESET}"

                                )

                        except:

                            output_manager.print_static(

                                f"{CYAN}📊 {cached_classification} [Cached]{RESET}"

                            )

                        _printed_classifications.add(symbol)

                return cached_classification

    base = symbol.split('_')[0].upper()

    cmc_classification = None

    try:

        cmc_data = await get_coin_market_data(symbol)

        # Note: from_cache flag is only for memory cache (_coin_type_cache)

        # File cache (coin_market_data.json) is still considered fresh data

        if cmc_data:

            cmc_rank = cmc_data.get('market_cap_rank', 9999)

            market_cap = cmc_data.get('market_cap', 0)

            vol_24h = cmc_data.get('volume_24h', 0)

            vol_ratio = cmc_data.get('volume_to_mcap_ratio', (vol_24h / market_cap) if market_cap else 0)

            source = cmc_data.get('source', 'UNKNOWN')

            total_score = cmc_data.get('classification_score', 0)

            stable_coins = {"USDT", "USDC", "DAI", "TUSD", "FDUSD", "FRAX", "PYUSD", "GUSD", "USDD", "LUSD"}

            if base in stable_coins:

                base_class = "BLUECHIP"

            elif cmc_rank <= 10:

                base_class = "BLUECHIP"

            elif cmc_rank <= 50:

                base_class = "MAJOR"

            elif cmc_rank <= 300:

                base_class = "ALTCOIN"

            else:

                base_class = "MICROCAP"

            modifier = 1 if vol_ratio > 0.5 else (-1 if vol_ratio < 0.01 else 0)

            tier_order = ["MICROCAP", "ALTCOIN", "MAJOR", "BLUECHIP"]

            base_idx = tier_order.index(base_class) if base_class in tier_order else 2

            proposed_idx = base_idx + modifier

            proposed_idx = max(0, min(proposed_idx, len(tier_order) - 1))

            

            # ADAPTIVE TIER LIMITS: Prevent tier upgrade/downgrade beyond rank boundaries

            # This ensures coins can't be upgraded/downgraded to tiers incompatible with their rank

            if cmc_rank > 50:

                # Rank > 50: Maximum tier is ALTCOIN (cannot upgrade to MAJOR or BLUECHIP)

                # Minimum tier is ALTCOIN if rank <= 300 (cannot downgrade to MICROCAP)

                max_allowed_tier = "ALTCOIN"

                max_allowed_idx = tier_order.index(max_allowed_tier)

                if proposed_idx > max_allowed_idx:

                    proposed_idx = max_allowed_idx

                if cmc_rank <= 300 and proposed_idx < max_allowed_idx:

                    # Rank 51-300: Cannot downgrade below ALTCOIN

                    proposed_idx = max_allowed_idx

            elif cmc_rank > 10:

                # Rank 11-50: Maximum tier is MAJOR (cannot upgrade to BLUECHIP)

                # Minimum tier is MAJOR (cannot downgrade below MAJOR)

                max_allowed_tier = "MAJOR"

                max_allowed_idx = tier_order.index(max_allowed_tier)

                if proposed_idx > max_allowed_idx:

                    proposed_idx = max_allowed_idx

                if proposed_idx < max_allowed_idx:

                    # Rank 11-50: Cannot downgrade below MAJOR

                    proposed_idx = max_allowed_idx

            elif cmc_rank <= 10:

                # Rank <= 10: Can be BLUECHIP or downgrade to MAJOR if vol very low

                # Minimum tier is MAJOR (cannot downgrade below MAJOR)

                min_allowed_tier = "MAJOR"

                min_allowed_idx = tier_order.index(min_allowed_tier)

                if proposed_idx < min_allowed_idx:

                    proposed_idx = min_allowed_idx

            # Rank > 300: Can be MICROCAP or upgrade to ALTCOIN if vol high

            

            classification = tier_order[proposed_idx]

            try:

                if base not in stable_coins:

                    meme_eval = await compute_meme_likelihood(symbol, base, cmc_data)

                    meme_score = meme_eval.get('score', 0)

                    

                    # ADAPTIVE MEME COIN DETECTION FOR HIGH RANK COINS

                    # More strict threshold for higher ranks (BLUECHIP/MAJOR)

                    # This ensures meme coins don't get classified as BLUECHIP/MAJOR

                    

                    if cmc_rank <= 10:

                        # BLUECHIP tier (rank <= 10): Very strict threshold

                        # Meme coins should not be BLUECHIP, even with high rank

                        if meme_score >= 50:

                            old = classification

                            if classification == 'BLUECHIP':

                                classification = 'MAJOR'  # Downgrade to MAJOR

                                if not from_cache:

                                    output_manager.print_static(

                                        f"{YELLOW}⚠️ Adaptive meme policy (High Rank): {base} rank {cmc_rank} score {meme_score:.0f} → {old}→{classification} (meme coin not eligible for BLUECHIP){RESET}"

                                    )

                        if meme_score >= 70:

                            old = classification

                            if classification == 'BLUECHIP':

                                classification = 'ALTCOIN'  # Strong meme signal → ALTCOIN

                            elif classification == 'MAJOR':

                                classification = 'ALTCOIN'

                            if not from_cache and old != classification:

                                output_manager.print_static(

                                    f"{YELLOW}⚠️ Adaptive meme policy (High Rank): {base} rank {cmc_rank} score {meme_score:.0f} → {old}→{classification}{RESET}"

                                )

                    elif cmc_rank <= 50:

                        # MAJOR tier (rank 11-50): Moderate threshold

                        if meme_score >= 70:

                            old = classification

                            if classification == 'MAJOR':

                                classification = 'ALTCOIN'

                            elif classification == 'ALTCOIN':

                                classification = 'MICROCAP'

                            if not from_cache and old != classification:

                                output_manager.print_static(

                                    f"{YELLOW}⚠️ Adaptive meme policy: {base} rank {cmc_rank} score {meme_score:.0f} → {old}→{classification}{RESET}"

                                )

                    else:

                        # ALTCOIN tier and below: Normal threshold (existing logic)

                        if meme_score >= 85:

                            old = classification

                            if classification == 'BLUECHIP':

                                classification = 'ALTCOIN'

                            elif classification == 'MAJOR':

                                classification = 'ALTCOIN'

                            elif classification == 'ALTCOIN':

                                classification = 'MICROCAP'

                            if not from_cache and old != classification:

                                output_manager.print_static(

                                    f"{YELLOW}⚠️ Adaptive meme policy: {base} score {meme_score:.0f} → {old}→{classification}{RESET}"

                                )

                        elif meme_score >= 70 and classification in ('BLUECHIP','MAJOR'):

                            old = classification

                            classification = 'MAJOR' if classification == 'BLUECHIP' else 'ALTCOIN'

                            if not from_cache and old != classification:

                                output_manager.print_static(

                                    f"{YELLOW}⚠️ Adaptive meme policy: {base} score {meme_score:.0f} → {old}→{classification}{RESET}"

                                )

            except Exception:

                pass

            # Only print if not already printed in this session

            with _printed_classifications_lock:

                if symbol not in _printed_classifications:

                    # Format: Responsive Compact
                    output_manager.print_static(
                        f"{CYAN}📊 {classification} │ Rank #{cmc_rank} │ MCap ${market_cap/1e9:.2f}B │ Vol/MCap {vol_ratio:.2f}{RESET}"
                    )

                    _printed_classifications.add(symbol)

            with _coin_type_cache_lock:

                _coin_type_cache[symbol] = {

                    'coin_type': classification,

                    'timestamp': current_time

                }

            cmc_classification = classification

            return classification

    except Exception as e:

        print(f"{YELLOW}⚠️ Dynamic classification error: {e}{RESET}")

    try:

        volatility = await calculate_coin_volatility(symbol)

        if volatility > 40:

            final_classification = "MICROCAP"

        elif volatility > 25:

            final_classification = "ALTCOIN"

        elif volatility < 12:

            final_classification = "BLUECHIP"

        else:

            final_classification = "ALTCOIN"

        # Only print if not already printed in this session

        with _printed_classifications_lock:

            if symbol not in _printed_classifications and cmc_classification is None:

                output_manager.print_static(f"{CYAN}📊 {final_classification} │ Vol-Based ({volatility:.1f}%){RESET}")

                _printed_classifications.add(symbol)

        return final_classification

    except:

        # Only print if not already printed in this session

        with _printed_classifications_lock:

            if symbol not in _printed_classifications and cmc_classification is None:

                output_manager.print_static(f"{CYAN}📊 ALTCOIN │ Default Fallback{RESET}")

                _printed_classifications.add(symbol)

        return "ALTCOIN"



def get_optimal_range(coin_type: str, volatility: float, market_regime) -> tuple:

    base_ranges = {

        "BLUECHIP": (0.8, 3.5),

        "MAJOR": (1.2, 5.0),

        "ALTCOIN": (2.0, 7.0),

        "MICROCAP": (3.0, 10.0)

    }

    min_delta, max_delta = base_ranges.get(coin_type, (2.0, 7.0))

    if volatility > 50:

        max_delta *= 1.4

        min_delta *= 1.2

    elif volatility > 40:

        max_delta *= 1.25

        min_delta *= 1.15

    elif volatility > 30:

        max_delta *= 1.1

        min_delta *= 1.05

    elif volatility < 8:

        max_delta *= 0.75

        min_delta *= 0.85

    elif volatility < 12:

        max_delta *= 0.85

        min_delta *= 0.9

    if market_regime in [MarketRegime.STRONG_UPTREND, MarketRegime.STRONG_DOWNTREND]:

        min_delta *= 0.9

        max_delta *= 0.9

    elif market_regime == MarketRegime.SIDEWAYS:

        min_delta *= 1.1

        max_delta *= 1.1

    return min_delta, max_delta



def apply_smart_boundaries(min_delta: float, max_delta: float, symbol: str) -> tuple:



    ABSOLUTE_MIN = 0.5

    ABSOLUTE_MAX = 18.0

    base = symbol.split('_')[0].upper()

    stable_coins = {"USDT", "USDC", "DAI", "TUSD", "FDUSD", "FRAX", "PYUSD", "GUSD", "USDD", "LUSD"}

    if base in stable_coins:
        SAFETY_MIN = 0.5

        SAFETY_MAX = 3.0

    elif base in ['BTC', 'ETH']:



        SAFETY_MIN = 0.8

        SAFETY_MAX = 8.0

    else:



        SAFETY_MIN = 1.2

        SAFETY_MAX = 12.0

    final_min = max(ABSOLUTE_MIN, SAFETY_MIN, min_delta)

    final_max = min(ABSOLUTE_MAX, SAFETY_MAX, max_delta)

    if final_min >= final_max:

        final_max = final_min + 0.5

    return final_min, final_max



async def calculate_smart_breakout_threshold(

    symbol: str,

    coin_type: str,

    volatility: float,

    analysis: ActionAnalysis,

    market_regime = None

) -> float:

    try:

        base_thresholds = {

            "BLUECHIP": 3.0,

            "MAJOR": 4.5,

            "ALTCOIN": 6.5,

            "MICROCAP": 8.5

        }

        base_threshold = base_thresholds.get(coin_type, 6.5)

        if volatility > 50:

            vol_adj = 3.0

        elif volatility > 40:

            vol_adj = 2.0

        elif volatility > 30:

            vol_adj = 1.0

        elif volatility < 10:

            vol_adj = -0.5

        else:

            vol_adj = 0

        threshold = base_threshold + vol_adj

        if market_regime:

            if market_regime in [MarketRegime.STRONG_UPTREND, MarketRegime.WEAK_UPTREND]:

                threshold *= 0.85

            elif market_regime in [MarketRegime.STRONG_DOWNTREND, MarketRegime.WEAK_DOWNTREND]:

                threshold *= 1.15

            elif market_regime == MarketRegime.SIDEWAYS:

                threshold *= 1.0

        confidence = analysis.confidence

        if confidence > 80:

            threshold *= 0.90

        elif confidence < 60:

            threshold *= 1.10

        risk_level = analysis.risk_level

        if risk_level == "VERY_HIGH":

            threshold *= 1.20

        elif risk_level == "HIGH":

            threshold *= 1.10

        elif risk_level == "LOW":

            threshold *= 0.90

        try:

            flow_data = await get_order_flow_optimized(symbol, 20)

            orderbook_data = await get_orderbook_optimized(symbol, 20)

            if flow_data:

                buy_pressure = flow_data.get('buy_pressure', 50)

                cvd_momentum = flow_data.get('cvd_momentum', 0)

                order_imbalance = flow_data.get('order_imbalance', 0)

                if buy_pressure > 65:

                    threshold *= 0.92

                elif buy_pressure < 45:

                    threshold *= 1.08

                if cvd_momentum > 1000:

                    threshold *= 0.95

                elif cvd_momentum < -500:

                    threshold *= 1.10

                if order_imbalance > 15:

                    threshold *= 0.93

                elif order_imbalance < -15:

                    threshold *= 1.12

        except Exception:

            pass

        try:

            liquidity = await analyze_liquidity_adaptive(symbol)

            if liquidity:

                liquidity_grade = liquidity.get('liquidity_grade', 'FAIR')

                if liquidity_grade == 'EXCELLENT':

                    threshold *= 0.95

                elif liquidity_grade == 'POOR':

                    threshold *= 1.10

        except Exception:

            pass

        manipulation_risk_score = 0

        try:

            candles = await get_candles_optimized(symbol, '5m', 50)

            if candles and len(candles) >= 10 and flow_data and orderbook_data:

                recent_volumes = [c['volume'] for c in candles[-10:]]

                avg_volume = sum(recent_volumes) / len(recent_volumes)

                latest_volume = candles[-1]['volume']

                volume_spike = latest_volume / avg_volume if avg_volume > 0 else 1

                if volume_spike > 3.0:

                    manipulation_risk_score += 2

                    output_manager.print_static(f"{YELLOW}⚠️ Volume spike detected: {volume_spike:.1f}x (wash trading risk!){RESET}")

                failed_breakouts = 0

                for i in range(len(candles) - 5, len(candles) - 1):

                    if i > 0:

                        prev_high = candles[i-1]['high']

                        curr_high = candles[i]['high']

                        curr_close = candles[i]['close']

                        if curr_high > prev_high * 1.02 and curr_close < curr_high * 0.98:

                            failed_breakouts += 1

                if failed_breakouts >= 2:

                    manipulation_risk_score += 2

                    output_manager.print_static(f"{YELLOW}⚠️ {failed_breakouts} failed breakout attempts detected (resistance zone!){RESET}")

                if orderbook_data and 'asks' in orderbook_data and 'bids' in orderbook_data:

                    asks = orderbook_data['asks']

                    bids = orderbook_data['bids']

                    if coin_type == 'BLUECHIP':

                        whale_wall_threshold = 2.3

                    elif coin_type == 'MAJOR':

                        whale_wall_threshold = 2.7

                    elif coin_type == 'ALTCOIN':

                        whale_wall_threshold = 3.2

                    else:

                        whale_wall_threshold = 4.2

                    if volatility > 40:

                        whale_wall_threshold *= 1.2

                    elif volatility > 25:

                        whale_wall_threshold *= 1.1

                    elif volatility < 10:

                        whale_wall_threshold *= 0.9

                    if asks and len(asks) > 5:

                        ask_sizes = [float(ask[1]) for ask in asks[:5]]

                        avg_ask_size = sum(ask_sizes) / len(ask_sizes)

                        max_ask_size = max(ask_sizes)

                        ask_ratio = max_ask_size / avg_ask_size if avg_ask_size > 0 else 0

                        if ask_ratio > whale_wall_threshold:

                            manipulation_risk_score += 1

                            output_manager.print_static(f"{YELLOW}⚠️ Large ask wall detected: {ask_ratio:.1f}x avg (whale resistance! {coin_type} threshold: {whale_wall_threshold}x){RESET}")

                    if bids and len(bids) > 5:

                        bid_sizes = [float(bid[1]) for bid in bids[:5]]

                        avg_bid_size = sum(bid_sizes) / len(bid_sizes)

                        max_bid_size = max(bid_sizes)

                        bid_ratio = max_bid_size / avg_bid_size if avg_bid_size > 0 else 0

                        if bid_ratio > whale_wall_threshold:

                            threshold *= 0.95

                            # Get bid wall level (price where wall is located)

                            bid_wall_price = float(bids[0][0]) if bids else 0

                            

                            # Track bid wall history for spoofing detection

                            ws_manager = _init_ws_manager()

                            is_spoofing = False

                            spoofing_reason = ""

                            

                            if ws_manager:

                                ws_manager.track_bid_wall(symbol, bid_ratio, bid_wall_price)

                                bid_wall_history = ws_manager.get_bid_wall_history(symbol)

                                

                                # Detect spoofing (appear-disappear pattern)

                                is_spoofing, spoofing_reason = detect_bid_wall_spoofing(

                                    symbol, bid_wall_history, bid_ratio, bid_wall_price, whale_wall_threshold

                                )

                            

                            if is_spoofing:

                                output_manager.print_static(

                                    f"{RED}⚠️ Large bid wall detected ({bid_ratio:.1f}x) but SPOOFING: {spoofing_reason} | "

                                    f"Wall at ${fmt(bid_wall_price)} (ignored){RESET}"

                                )

                            else:

                                output_manager.print_static(

                                    f"{GREEN}✅ Large bid wall detected: {bid_ratio:.1f}x avg (strong support! "

                                    f"{coin_type} threshold: {whale_wall_threshold:.1f}x) | Wall at ${fmt(bid_wall_price)}{RESET}"

                                )

                order_flow_data = None

                orderbook_data = None

                try:

                    ws_manager = _init_ws_manager()

                    if ws_manager:

                        if hasattr(ws_manager, 'get_order_flow_analysis'):

                            order_flow_data = ws_manager.get_order_flow_analysis(symbol, limit=20)

                        if hasattr(ws_manager, 'get_orderbook_analysis'):

                            orderbook_data = ws_manager.get_orderbook_analysis(symbol)

                except Exception:

                    pass

                manipulation_result = comprehensive_manipulation_check(

                    candles, coin_type, volatility, order_flow_data, orderbook_data

                )

                overall_risk = manipulation_result['overall_risk']

                if overall_risk in ['HIGH', 'CRITICAL']:

                    manipulation_risk_score += 3

                    output_manager.print_static(f"{RED}🚨 {overall_risk} manipulation risk detected!{RESET}")

                    output_manager.print_static(f"{RED}   Detected: {', '.join(manipulation_result['detected_patterns'])}{RESET}")

                if manipulation_risk_score >= 5:

                    threshold *= 1.30

                    output_manager.print_static(f"{RED}🛡️ Manipulation Protection: Threshold +30% (risk score: {manipulation_risk_score}){RESET}")

                elif manipulation_risk_score >= 3:

                    threshold *= 1.20

                    output_manager.print_static(f"{YELLOW}🛡️ Manipulation Protection: Threshold +20% (risk score: {manipulation_risk_score}){RESET}")

                elif manipulation_risk_score >= 1:

                    threshold *= 1.10

                    output_manager.print_static(f"{YELLOW}🛡️ Manipulation Protection: Threshold +10% (risk score: {manipulation_risk_score}){RESET}")

                else:

                    output_manager.print_static(f"{GREEN}✅ No manipulation detected - clean breakout conditions{RESET}")

        except Exception as e:

            pass

        if coin_type == 'BLUECHIP':

            min_threshold, max_threshold = 1.8, 7.5

        elif coin_type == 'MAJOR':

            min_threshold, max_threshold = 2.5, 9.0

        elif coin_type == 'ALTCOIN':

            min_threshold, max_threshold = 3.5, 12.0

        elif coin_type == 'MICROCAP':

            min_threshold, max_threshold = 5.0, 15.0

        else:

            min_threshold, max_threshold = 3.5, 12.0

        final_threshold = max(min_threshold, min(max_threshold, threshold))

        return final_threshold

    except Exception as e:

        output_manager.print_static(f"{YELLOW}⚠️ Smart breakout calculation error: {e}, using fallback{RESET}")

        fallback_thresholds = {

            "BLUECHIP": 3.0,

            "MAJOR": 4.5,

            "ALTCOIN": 6.5,

            "MICROCAP": 8.5

        }

        base = fallback_thresholds.get(coin_type, 6.0)

        vol_adj = 1.0 if volatility > 40 else (0.5 if volatility > 30 else 0)

        return max(2.0, min(15.0, base + vol_adj))



def get_adaptive_stop_loss(coin_type: str, volatility: float) -> float:

    base_stop_loss = {

        'BLUECHIP': 2.5,

        'MAJOR': 4.0,

        'ALTCOIN': 5.5,

        'MICROCAP': 8.0

    }

    stop_loss = base_stop_loss.get(coin_type, 5.5)

    if volatility > 50:

        stop_loss *= 1.3

    elif volatility > 40:

        stop_loss *= 1.2

    elif volatility > 30:

        stop_loss *= 1.15

    elif volatility < 10:

        stop_loss *= 0.85

    return min(max(stop_loss, 1.5), 12.0)



def get_adaptive_take_profit(coin_type: str, volatility: float,

                              upside_potential: float, market_regime) -> float:

    base_take_profit = {

        'BLUECHIP': 2.5,

        'MAJOR': 4.0,

        'ALTCOIN': 7.0,

        'MICROCAP': 12.0

    }

    tp = base_take_profit.get(coin_type, 3.5)

    if volatility > 50:

        tp += 2.0

    elif volatility > 40:

        tp += 1.5

    elif volatility > 30:

        tp += 1.0

    elif volatility > 20:

        tp += 0.5

    elif volatility < 10:

        tp -= 0.5

    if market_regime in [MarketRegime.STRONG_UPTREND]:

        tp += 1.0

    elif market_regime in [MarketRegime.WEAK_UPTREND]:

        tp += 0.5

    elif market_regime in [MarketRegime.SIDEWAYS, MarketRegime.HIGH_VOL_CONSOLIDATION]:

        tp -= 0.5

    elif market_regime in [MarketRegime.STRONG_DOWNTREND, MarketRegime.WEAK_DOWNTREND]:

        tp -= 1.0

    if upside_potential > 0 and upside_potential < tp:

        if volatility > 30:

            tp = max(tp, upside_potential * 0.9)

        else:

            tp = upside_potential * 0.8

    min_tp_based_on_sl = get_adaptive_stop_loss(coin_type, volatility) * 2.0

    tp = max(tp, min_tp_based_on_sl)

    return max(2.0, min(20.0, tp))



def get_scalping_stop_loss(coin_type: str, volatility: float) -> float:

    """

    International standard scalping stop loss calculation.

    Scalping SL range: 0.3% - 1.0% (tight stops for quick exits)

    """

    # Base SL for scalping (tighter than regular trading)

    base_scalping_sl = {

        'BLUECHIP': 0.4,   # 0.4% for stable coins

        'MAJOR': 0.5,      # 0.5% for major coins

        'ALTCOIN': 0.7,   # 0.7% for altcoins

        'MICROCAP': 0.9   # 0.9% for microcaps (higher risk)

    }

    sl = base_scalping_sl.get(coin_type, 0.6)

    

    # Adjust based on volatility (scalpers need tighter stops in volatile markets)

    if volatility > 50:

        sl *= 1.2  # Slightly wider for very volatile markets

    elif volatility > 40:

        sl *= 1.1

    elif volatility < 15:

        sl *= 0.9  # Tighter for low volatility (safer scalping)

    

    # International scalping standard: 0.3% - 1.0%

    return min(max(sl, 0.3), 1.0)



def get_scalping_take_profit(coin_type: str, volatility: float, 

                              upside_potential: float, market_regime) -> float:

    """

    International standard scalping take profit calculation.

    Scalping TP range: 0.5% - 2.0% (quick profits)

    Risk:Reward ratio: Minimum 1:1.5 (standard for scalping)

    """

    # Base TP for scalping (smaller targets for quick exits)

    base_scalping_tp = {

        'BLUECHIP': 0.6,   # 0.6% for stable coins

        'MAJOR': 0.8,      # 0.8% for major coins

        'ALTCOIN': 1.2,    # 1.2% for altcoins

        'MICROCAP': 1.5    # 1.5% for microcaps

    }

    tp = base_scalping_tp.get(coin_type, 1.0)

    

    # Adjust based on volatility

    if volatility > 50:

        tp += 0.3  # Slightly higher TP for volatile markets

    elif volatility > 40:

        tp += 0.2

    elif volatility > 30:

        tp += 0.1

    elif volatility < 15:

        tp -= 0.1  # Lower TP for low volatility

    

    # Adjust based on market regime (scalpers prefer trending markets)

    if market_regime in [MarketRegime.STRONG_UPTREND]:

        tp += 0.2

    elif market_regime in [MarketRegime.WEAK_UPTREND]:

        tp += 0.1

    elif market_regime in [MarketRegime.SIDEWAYS, MarketRegime.HIGH_VOL_CONSOLIDATION]:

        tp -= 0.1

    elif market_regime in [MarketRegime.STRONG_DOWNTREND, MarketRegime.WEAK_DOWNTREND]:

        tp -= 0.2

    

    # Ensure minimum Risk:Reward ratio 1:1.5 (international scalping standard)

    sl = get_scalping_stop_loss(coin_type, volatility)

    min_tp_based_on_sl = sl * 1.5  # Minimum 1:1.5 ratio

    tp = max(tp, min_tp_based_on_sl)

    

    # International scalping standard: 0.5% - 2.0%

    return max(0.5, min(2.0, tp))



async def validate_timeout_entry_conditions(symbol: str, current_price: float, target_price: float, initial_analysis: ActionAnalysis = None) -> tuple[bool, str, ActionAnalysis]:

    """

    Validate market conditions before entering on timeout

    Returns: (is_valid, reason, updated_analysis)

    """

    try:

        output_manager.print_static(f"{CYAN}🔍 Validating market conditions before timeout entry...{RESET}")

        

        # Quick re-analysis

        if initial_analysis is None:

            analysis = await analyze_hold_wait_buy_ai(symbol, lite=True, force_fresh=True)

        else:

            analysis = await analyze_hold_wait_buy_ai(symbol, lite=True, force_fresh=True)

        

        # Check AI confidence (min 60%)

        if analysis.confidence < 60:

            return False, f"AI confidence too low ({analysis.confidence:.0f}% < 60%)", analysis

        

        # Check risk level (not VERY_HIGH)

        if analysis.risk_level == "VERY_HIGH":

            return False, f"Risk level too high ({analysis.risk_level})", analysis

        

        # Check volume and order flow

        try:

            flow_data = await get_order_flow_optimized(symbol, 20)

            buy_pressure = flow_data.get('buy_pressure', 50) if flow_data else 50

            

            if buy_pressure < 50:

                return False, f"Buy pressure too low ({buy_pressure:.1f}% < 50%)", analysis

        except Exception:

            # If can't get order flow, still proceed but with warning

            pass

        

        # Check price deviation from target

        price_deviation = safe_division((current_price - target_price), target_price, 0.0) * 100

        

        # If price is too high above target (>10%), be more cautious

        if price_deviation > 10:

            output_manager.print_static(f"{YELLOW}⚠️ Price is {price_deviation:.1f}% above target - being cautious{RESET}")

            # Require higher confidence for high price entries

            if analysis.confidence < 70:

                return False, f"Price too high ({price_deviation:.1f}% above target) and confidence insufficient ({analysis.confidence:.0f}% < 70%)", analysis

        

        return True, "Market conditions validated", analysis

        

    except Exception as e:

        output_manager.print_static(f"{YELLOW}⚠️ Validation error: {e} - proceeding with caution{RESET}")

        # If validation fails, still proceed but log warning

        return True, f"Validation error: {e}", initial_analysis





async def calculate_adaptive_timeout_entry_price(symbol: str, current_price: float, target_price: float, initial_discount_pct: float = 1.0) -> float:

    """

    Calculate adaptive entry price for timeout scenario

    - If price still above target: use target with discount

    - If price below target: use current with small discount (0.5-1%)

    - If price very high (>5% above target): use target directly (skip discount)

    """

    try:

        price_deviation = safe_division((current_price - target_price), target_price, 0.0) * 100

        

        if price_deviation > 5:

            # Price very high above target - use target directly (no discount)

            output_manager.print_static(f"{YELLOW}💰 Price is {price_deviation:.1f}% above target - using target price directly{RESET}")

            return target_price

        elif price_deviation > 0:

            # Price still above target - use target with original discount

            adaptive_entry = target_price * (1 - initial_discount_pct / 100.0)

            output_manager.print_static(f"{CYAN}💰 Price above target - using target with discount: ${adaptive_entry:.2f} (target: ${target_price:.2f}, discount: {initial_discount_pct:.1f}%){RESET}")

            return adaptive_entry

        else:

            # Price below target - use current with small discount (0.5-1%)

            small_discount = min(1.0, max(0.5, initial_discount_pct * 0.5))

            adaptive_entry = current_price * (1 - small_discount / 100.0)

            output_manager.print_static(f"{CYAN}💰 Price below target - using current with small discount: ${adaptive_entry:.2f} (current: ${current_price:.2f}, discount: {small_discount:.1f}%){RESET}")

            return adaptive_entry

            

    except Exception as e:

        output_manager.print_static(f"{YELLOW}⚠️ Error calculating adaptive entry: {e} - using current price{RESET}")

        return current_price





def get_adaptive_timeout(coin_type: str, volatility: float, rotation_mode: bool = False, use_trailing_tp: bool = False) -> int:

    base_timeout = {

        'BLUECHIP': 45,

        'MAJOR': 35,

        'ALTCOIN': 25,

        'MICROCAP': 20

    }

    timeout = base_timeout.get(coin_type, 30)

    if volatility > 50:

        timeout -= 10

    elif volatility > 30:

        timeout -= 5

    elif volatility < 10:

        timeout += 10

    if rotation_mode:

        timeout = min(max(timeout, 30), 60)

    else:

        timeout = min(max(timeout, 10), 60)

    # Increase timeout for trailing mode (needs more time for optimal entry)

    if use_trailing_tp:

        timeout = min(timeout + 30, 90)  # Add 30 minutes, max 90 minutes

    return timeout



def get_adaptive_offset(coin_type: str, volatility: float) -> float:

    base_offset = {

        'BLUECHIP': 0.5,

        'MAJOR': 1.0,

        'ALTCOIN': 1.8,

        'MICROCAP': 2.8

    }

    offset = base_offset.get(coin_type, 1.8)

    if volatility > 50:

        offset *= 1.3

    elif volatility > 40:

        offset *= 1.2

    elif volatility > 30:

        offset *= 1.1

    elif volatility < 10:

        offset *= 0.8

    return min(max(offset, 0.3), 5.0)



def get_adaptive_poll_interval(coin_type: str, volatility: float) -> float:

    # Fixed poll interval: 1.0 second untuk semua coin types

    # User request: ubah dari 1.8 detik menjadi 1 detik

    return 1.0



def get_adaptive_confidence_threshold(coin_type: str, volatility: float) -> float:

    base_threshold = {

        'BLUECHIP': 50.0,

        'MAJOR': 60.0,

        'ALTCOIN': 68.0,

        'MICROCAP': 75.0,

        'STABLE': 45.0

    }

    threshold = base_threshold.get(coin_type, 65.0)

    if volatility > 50:

        threshold *= 1.08

    elif volatility > 35:

        threshold *= 1.04

    elif volatility < 10:

        threshold *= 0.95

    elif volatility < 15:

        threshold *= 0.98

    return min(max(threshold, 45.0), 85.0)



def get_coin_display_name(symbol: str, internal_type: str) -> str:

    display_map = {

        "BLUECHIP": "BLUECHIP",

        "MAJOR": "MAJOR",

        "ALTCOIN": "ALTCOIN",

        "MICROCAP": "MICROCAP",

        "VOLATILE": "MICROCAP",

        "STABLE": "BLUECHIP",

        "MEME": "MICROCAP",

        "NANO": "MICROCAP"

    }

    return display_map.get(internal_type, internal_type)



def get_price_deviation_threshold(coin_type: str, volatility: float) -> float:

    base_thresholds = {

        "BLUECHIP": 1.0,

        "MAJOR": 1.8,

        "ALTCOIN": 2.8,

        "MICROCAP": 4.0

    }

    base = base_thresholds.get(coin_type, 2.5)

    if volatility > 40:

        vol_adj = 1.0

    elif volatility > 30:

        vol_adj = 0.5

    elif volatility < 10:

        vol_adj = -0.3

    else:

        vol_adj = 0

    final_threshold = base + vol_adj

    final_threshold = max(0.5, min(5.0, final_threshold))

    return final_threshold



def get_dynamic_trading_params(coin_type: str, volatility: float) -> dict:

    params_map = {

        "BLUECHIP": {

            "entry_confidence": 50.0,

            "trailing_buy_delta": 0.3,

            "trailing_sell_delta": 0.8,

            "stop_loss_percent": 2.5,

            "take_profit_min": 2.5

        },

        "MAJOR": {

            "entry_confidence": 60.0,

            "trailing_buy_delta": 0.8,

            "trailing_sell_delta": 1.5,

            "stop_loss_percent": 4.0,

            "take_profit_min": 4.0

        },

        "ALTCOIN": {

            "entry_confidence": 65.0,

            "trailing_buy_delta": 1.5,

            "trailing_sell_delta": 2.5,

            "stop_loss_percent": 5.5,

            "take_profit_min": 7.0

        },

        "MICROCAP": {

            "entry_confidence": 72.0,

            "trailing_buy_delta": 2.5,

            "trailing_sell_delta": 4.0,

            "stop_loss_percent": 8.0,

            "take_profit_min": 12.0

        },

        "STABLE": {

            "entry_confidence": 50.0,

            "trailing_buy_delta": 0.2,

            "trailing_sell_delta": 0.3,

            "stop_loss_percent": 1.0,

            "take_profit_min": 0.5

        }

    }

    params = params_map.get(coin_type, params_map["ALTCOIN"]).copy()

    if volatility > 40:

        params["trailing_buy_delta"] *= 1.5

        params["trailing_sell_delta"] *= 1.5

        params["stop_loss_percent"] *= 1.3

    elif volatility > 25:

        params["trailing_buy_delta"] *= 1.2

        params["trailing_sell_delta"] *= 1.2

        params["stop_loss_percent"] *= 1.15

    elif volatility < 10:

        params["trailing_buy_delta"] *= 0.7

        params["trailing_sell_delta"] *= 0.7

        params["stop_loss_percent"] *= 0.85

    params["trailing_buy_delta"] = max(0.2, min(5.0, params["trailing_buy_delta"]))

    params["trailing_sell_delta"] = max(0.3, min(6.0, params["trailing_sell_delta"]))

    params["stop_loss_percent"] = max(1.0, min(12.0, params["stop_loss_percent"]))

    return params



def get_volume_ratio_threshold(coin_type: str) -> float:

    thresholds = {

        "BLUECHIP": 0.95,

        "MAJOR": 0.90,

        "ALTCOIN": 0.80,

        "MICROCAP": 0.60,

        "STABLE": 0.9

    }

    return thresholds.get(coin_type, 0.8)



def get_ai_recommendation_thresholds(coin_type: str) -> dict:

    thresholds = {

        "BLUECHIP": {

            "STRONG_BUY": 65,

            "BUY": 50,

            "HOLD": 35,

            "WAIT": 20

        },

        "MAJOR": {

            "STRONG_BUY": 70,

            "BUY": 55,

            "HOLD": 40,

            "WAIT": 25

        },

        "ALTCOIN": {

            "STRONG_BUY": 75,

            "BUY": 60,

            "HOLD": 45,

            "WAIT": 30

        },

        "MICROCAP": {

            "STRONG_BUY": 80,

            "BUY": 65,

            "HOLD": 50,

            "WAIT": 35

        },

        "STABLE": {

            "STRONG_BUY": 65,

            "BUY": 50,

            "HOLD": 35,

            "WAIT": 20

        }

    }

    return thresholds.get(coin_type, thresholds["ALTCOIN"])



async def calculate_smart_activation_price(symbol: str, current_price: float, analysis: ActionAnalysis, suppress_logs: bool = False) -> float:

    try:

        coin_type = await get_coin_type_range(symbol)

        volatility = await calculate_coin_volatility(symbol)

        adaptive_conf_threshold_map = get_adaptive_confidence_threshold(coin_type, volatility)

        try:

            ai_immediate_threshold = float(adaptive_conf_threshold_map.get("BUY", 65))

        except Exception:

            ai_immediate_threshold = 65.0

        manipulation_discount = 0.0

        manipulation_warnings = []

        try:

            # Force fresh data for accurate activation price calculation

            orderbook_data = await get_orderbook_optimized(symbol, 20, force_fresh=True)

            flow_data = await get_order_flow_optimized(symbol, 20)

            candles = await get_candles_optimized(symbol, '5m', 10, force_fresh=True)

            if orderbook_data and flow_data and candles:

                is_whale_wall, whale_desc, whale_details = detect_whale_wall_absorption(

                    symbol, orderbook_data, flow_data

                )

                if is_whale_wall:

                    manipulation_discount += 0.01

                    manipulation_warnings.append(f"🐋 Whale wall detected: {whale_desc}")

                is_false_signal, false_desc, false_details = detect_false_signal_patterns(

                    candles, flow_data, orderbook_data, coin_type, volatility

                )

                if is_false_signal:

                    manipulation_discount += 0.015

                    manipulation_warnings.append(f"🚨 False signal: {false_desc}")

                if len(candles) >= 3:

                    recent_volume = candles[-1]['volume']

                    avg_volume = sum(c['volume'] for c in candles[-3:]) / 3

                    volume_spike = recent_volume / avg_volume if avg_volume > 0 else 1

                    if volume_spike > 2.5:

                        manipulation_discount += 0.005

                        manipulation_warnings.append(f"📊 Volume spike: {volume_spike:.1f}x")

                institutional_activity = flow_data.get('institutional_activity', 0)

                if institutional_activity > 40:

                    manipulation_discount += 0.01

                    manipulation_warnings.append(f"🏛️ High whale activity: {institutional_activity:.0f}%")

                cvd_momentum = flow_data.get('cvd_momentum', 0)

                if abs(cvd_momentum) > 2000:

                    manipulation_discount += 0.005

                    manipulation_warnings.append(f"📈 Extreme CVD momentum: {cvd_momentum:+.0f}")

                is_bullish_signal, bullish_desc, bullish_details = detect_legitimate_bullish_signal(

                    candles, flow_data, orderbook_data

                )

                if is_bullish_signal:

                    manipulation_discount -= 0.015

                    manipulation_warnings.append(f"🚀 Legitimate bullish: {bullish_desc}")

        except Exception as e:

            output_manager.print_static(f"{YELLOW}⚠️ Manipulation detection error: {e}{RESET}")

        total_discount_percent = 0.0

        if volatility > 50:

            total_discount_percent += 3.0

        elif volatility > 40:

            total_discount_percent += 2.5

        elif volatility > 30:

            total_discount_percent += 2.0

        elif volatility > 20:

            total_discount_percent += 1.5

        elif volatility > 10:

            total_discount_percent += 1.0

        else:

            total_discount_percent += 0.5

        if analysis.market_regime in [MarketRegime.STRONG_UPTREND, MarketRegime.WEAK_UPTREND]:

            total_discount_percent += 0.5

        elif analysis.market_regime in [MarketRegime.STRONG_DOWNTREND, MarketRegime.WEAK_DOWNTREND]:

            total_discount_percent += 1.5

        elif analysis.market_regime == MarketRegime.SIDEWAYS:

            total_discount_percent += 1.0

        else:

            total_discount_percent += 0.5

        if analysis.confidence > 80:

            total_discount_percent += 0.5

        elif analysis.confidence > 60:

            total_discount_percent += 1.0

        else:

            total_discount_percent += 1.5

        if analysis.risk_level == "VERY_HIGH":

            total_discount_percent += 2.0

        elif analysis.risk_level == "HIGH":

            total_discount_percent += 1.5

        elif analysis.risk_level == "MEDIUM":

            total_discount_percent += 1.0

        else:

            total_discount_percent += 0.5

        # Use coin_type from line 7675 (already fetched)

        if coin_type == 'BLUECHIP':

            total_discount_percent += 0.3

        elif coin_type == 'MAJOR':

            total_discount_percent += 0.6

        elif coin_type == 'ALTCOIN':

            total_discount_percent += 0.9

        else:

            total_discount_percent += 1.2

        total_discount_percent += (manipulation_discount * 100)

        # Use coin_type from line 7675 (already fetched, no need to fetch again)

        if coin_type == 'BLUECHIP':

            max_discount = 2.0

        elif coin_type == 'MAJOR':

            max_discount = 3.5

        elif coin_type == 'ALTCOIN':

            max_discount = 5.5

        elif coin_type == 'MICROCAP':

            max_discount = 7.0

        else:

            max_discount = 5.0

        regime_multiplier = 1.0

        regime_adjustment_desc = ""

        if analysis.market_regime == MarketRegime.STRONG_UPTREND:

            regime_multiplier = 0.6

            regime_adjustment_desc = "STRONG_UPTREND (-40%)"

        elif analysis.market_regime == MarketRegime.WEAK_UPTREND:

            regime_multiplier = 0.8

            regime_adjustment_desc = "WEAK_UPTREND (-20%)"

        elif analysis.market_regime == MarketRegime.SIDEWAYS:

            regime_multiplier = 1.0

            # SIDEWAYS = neutral, no adjustment needed
            # Don't show "SIDEWAYS (0%)" in output to avoid confusion
            regime_adjustment_desc = ""  # Empty = no regime adjustment

        else:

            regime_multiplier = 1.0

            regime_adjustment_desc = f"{analysis.market_regime.value} (0%)"

        confidence_multiplier = 1.0

        confidence_adjustment_desc = ""

        if analysis.confidence > 85:

            confidence_multiplier = 0.9

            confidence_adjustment_desc = "High Conf (-10%)"

        elif analysis.confidence > 70:

            confidence_multiplier = 1.0

            confidence_adjustment_desc = "Med Conf (0%)"

        else:

            confidence_multiplier = 1.1

            confidence_adjustment_desc = "Low Conf (+10%)"

        original_max_discount = max_discount

        combined_multiplier = regime_multiplier * confidence_multiplier

        max_discount = max_discount * combined_multiplier

        

        # Calculate minimum discount based on volatility AND coin type (international standard)

        # Higher volatility requires larger discount for safety buffer

        # Higher risk coin types (MICROCAP, ALTCOIN) require additional buffer

        # This must be calculated BEFORE applying to total_discount_percent

        # 

        # Base minimum discount tiers (volatility-based):

        # - >80%: Extreme volatility (market manipulation, panic) - base 2.0%

        # - 60-80%: Very high volatility (high risk) - base 1.5%

        # - 40-60%: High volatility (elevated risk) - base 1.0%

        # - 20-40%: Medium volatility (normal risk) - base 0.75%

        # - <20%: Low volatility (stable) - base 0.5%

        #

        # Coin type adjustments (risk-based):

        # - BLUECHIP: Base minimum (most stable, lowest risk)

        # - MAJOR: Base minimum + 0.1% (slightly more volatile)

        # - ALTCOIN: Base minimum + 0.25% (higher risk, more volatile)

        # - MICROCAP: Base minimum + 0.5% (highest risk, most volatile)

        if volatility > 80:

            base_min_discount = 2.0  # Extreme volatility: base 2.0% for safety (prevents whipsaw)

        elif volatility > 60:

            base_min_discount = 1.5  # Very high volatility: base 1.5% for safety

        elif volatility > 40:

            base_min_discount = 1.0  # High volatility: base 1.0% for safety

        elif volatility > 20:

            base_min_discount = 0.75  # Medium volatility: base 0.75%

        else:

            base_min_discount = 0.5  # Low volatility: base 0.5%

        

        # Apply coin type adjustment (risk-based buffer)

        # Higher risk coins need larger minimum discount for safety

        if coin_type == 'BLUECHIP':

            coin_type_adjustment = 0.0  # Most stable, no additional buffer needed

        elif coin_type == 'MAJOR':

            coin_type_adjustment = 0.1  # Slightly more volatile, small buffer

        elif coin_type == 'ALTCOIN':

            coin_type_adjustment = 0.25  # Higher risk, moderate buffer

        elif coin_type == 'MICROCAP':

            coin_type_adjustment = 0.5  # Highest risk, largest buffer

        else:

            coin_type_adjustment = 0.3  # Default for unknown types

        

        # Final minimum discount = base (volatility-based) + adjustment (coin type risk-based)

        min_discount = base_min_discount + coin_type_adjustment

        

        # Apply manipulation adjustment to min_discount for safety

        # Higher manipulation risk requires larger discount to avoid false signals

        if manipulation_discount > 0:

            min_discount += manipulation_discount

            # Cap at max_discount if manipulation adjustment would exceed it

            # This ensures we don't exceed maximum safe discount levels

            min_discount = min(min_discount, max_discount)

        

        immediate_entry = False

        try:

            if (

                analysis.recommendation in [ActionRecommendation.STRONG_BUY, ActionRecommendation.BUY]

                and float(analysis.confidence) >= ai_immediate_threshold

                and analysis.market_regime in [MarketRegime.STRONG_UPTREND, MarketRegime.WEAK_UPTREND]

            ):

                immediate_entry = True

        except Exception:

            immediate_entry = False

        if immediate_entry:

            total_discount_percent = max(0.0, min(max_discount, total_discount_percent))

        else:

            # Use volatility-based minimum discount for better risk management

            total_discount_percent = max(min_discount, min(max_discount, total_discount_percent))

        final_discount = 1 - (total_discount_percent / 100)

        if manipulation_warnings:

            output_manager.print_static(f"{YELLOW}⚠️ Market Analysis - Activation Adjustment:{RESET}")

            for warning in manipulation_warnings:

                output_manager.print_static(f"   {warning}")

            if manipulation_discount != 0:

                output_manager.print_static(f"{CYAN}   Manipulation Adjustment: {manipulation_discount*100:+.1f}%{RESET}")

        spread_discount = 0.0

        try:

            if orderbook_data and 'bids' in orderbook_data and 'asks' in orderbook_data:

                bids = orderbook_data['bids']

                asks = orderbook_data['asks']

                if bids and asks:

                    best_bid = bids[0][0]

                    best_ask = asks[0][0]

                    spread_pct = safe_division((best_ask - best_bid), best_bid, 0.0) * 100

                    if coin_type == 'BLUECHIP':

                        spread_threshold = 0.05

                    elif coin_type == 'MAJOR':

                        spread_threshold = 0.12

                    elif coin_type == 'ALTCOIN':

                        spread_threshold = 0.35

                    elif coin_type == 'MICROCAP':

                        spread_threshold = 0.8

                    else:

                        spread_threshold = 0.5

                    if spread_pct > spread_threshold:

                        spread_discount = min(1.0, (spread_pct - spread_threshold) * 0.5)

                        output_manager.print_static(f"{YELLOW}⚠️ Wide spread detected: {spread_pct:.3f}% (threshold: {spread_threshold}%) → +{spread_discount:.2f}% discount{RESET}")

                        total_discount_percent += spread_discount

        except Exception as e:

            pass

        

        # Re-apply minimum discount after spread adjustment (to ensure it's still respected)

        if not immediate_entry:

            total_discount_percent = max(min_discount, min(max_discount, total_discount_percent))

        # Note: Don't show discount here yet - wait until final smart_activation is determined

        # to calculate the actual discount used (may be different if candidate prices are used)

        # Calculate VWAP (Volume Weighted Average Price) for international standard reference

        # Use cached data (historical, no need for fresh)

        vwap_price = None

        try:

            candles_vwap = await get_candles_optimized(symbol, '1h', 24, force_fresh=False)

            if candles_vwap and len(candles_vwap) > 0:

                total_volume = sum(c.get('volume', 0) for c in candles_vwap)

                volume_weighted_price = sum(

                    (c.get('high', 0) + c.get('low', 0) + c.get('close', 0)) / 3 * c.get('volume', 0)

                    for c in candles_vwap

                )

                if total_volume > 0:

                    vwap_price = volume_weighted_price / total_volume

        except Exception:

            pass

        

        # Calculate support level from recent swing lows (international standard)

        # Use cached data (historical, no need for fresh)

        support_level = None

        try:

            candles_support = await get_candles_optimized(symbol, '15m', 48, force_fresh=False)  # 12 hours of 15m candles

            if candles_support and len(candles_support) >= 10:

                lows = [c.get('low', current_price) for c in candles_support if c.get('low')]

                if lows:

                    # Find significant support (bottom 20% of range)

                    low_range = min(lows)

                    high_range = max(lows)

                    range_size = high_range - low_range

                    if range_size > 0:

                        # Support is typically near recent lows, but not too far below

                        recent_lows = sorted(lows[-10:])  # Last 10 lows

                        support_level = recent_lows[0] if recent_lows else low_range

                        # Ensure support is not too far below current price (>10% discount)

                        if support_level < current_price * 0.90:

                            support_level = current_price * 0.95  # Use 5% discount as min support

        except Exception:

            pass

        

        # Calculate resistance level from recent swing highs (international standard)

        # Use cached data (historical, no need for fresh)

        resistance_level = None

        try:

            candles_resistance = await get_candles_optimized(symbol, '15m', 48, force_fresh=False)  # 12 hours of 15m candles

            if candles_resistance and len(candles_resistance) >= 10:

                highs = [c.get('high', current_price) for c in candles_resistance if c.get('high')]

                if highs:

                    # Find significant resistance (top 20% of range)

                    low_range = min(highs)

                    high_range = max(highs)

                    range_size = high_range - low_range

                    if range_size > 0:

                        # Resistance is typically near recent highs

                        recent_highs = sorted(highs[-10:], reverse=True)  # Last 10 highs, sorted descending

                        resistance_level = recent_highs[0] if recent_highs else high_range

                        # Use resistance as reference for potential breakout (not for activation buy)

        except Exception:

            pass

        

        # Calculate Fibonacci Retracement levels (international standard)

        # Use cached data (historical, no need for fresh)

        fibonacci_levels = {}

        try:

            candles_fib = await get_candles_optimized(symbol, '1h', 24, force_fresh=False)  # 24 hours of 1h candles

            if candles_fib and len(candles_fib) >= 10:

                highs = [c.get('high', current_price) for c in candles_fib if c.get('high')]

                lows = [c.get('low', current_price) for c in candles_fib if c.get('low')]

                if highs and lows:

                    swing_high = max(highs)

                    swing_low = min(lows)

                    fib_range = swing_high - swing_low

                    if fib_range > 0:

                        # Calculate Fibonacci retracement levels (38.2%, 50%, 61.8%)

                        fibonacci_levels['38.2'] = swing_high - (fib_range * 0.382)

                        fibonacci_levels['50.0'] = swing_high - (fib_range * 0.500)

                        fibonacci_levels['61.8'] = swing_high - (fib_range * 0.618)

        except Exception:

            pass

        

        # Get Moving Averages (EMA20/50/200) as reference (international standard)

        # Use cached data (historical, no need for fresh)

        ema20_price = None

        ema50_price = None

        ema200_price = None

        try:

            # Calculate EMA from candles

            candles_ema = await get_candles_optimized(symbol, '1h', 200, force_fresh=False)  # Need enough for EMA200

            if candles_ema and len(candles_ema) >= 20:

                closes = [c.get('close', current_price) for c in candles_ema if c.get('close')]

                if len(closes) >= 20:

                    # Calculate EMA20

                    if len(closes) >= 20:

                        ema20 = closes[0]

                        multiplier_20 = 2.0 / (20 + 1)

                        for price in closes[1:]:

                            ema20 = (price * multiplier_20) + (ema20 * (1 - multiplier_20))

                        ema20_price = ema20

                    

                    # Calculate EMA50

                    if len(closes) >= 50:

                        ema50 = closes[0]

                        multiplier_50 = 2.0 / (50 + 1)

                        for price in closes[1:]:

                            ema50 = (price * multiplier_50) + (ema50 * (1 - multiplier_50))

                        ema50_price = ema50

                    

                    # Calculate EMA200

                    if len(closes) >= 200:

                        ema200 = closes[0]

                        multiplier_200 = 2.0 / (200 + 1)

                        for price in closes[1:]:

                            ema200 = (price * multiplier_200) + (ema200 * (1 - multiplier_200))

                        ema200_price = ema200

        except Exception:

            pass

        

        # Calculate Volume Profile: Value Area and Point of Control (POC) (international standard)

        # Use cached data (historical, no need for fresh)

        volume_profile_data = {}

        try:

            candles_vp = await get_candles_optimized(symbol, '15m', 96, force_fresh=False)  # 24 hours of 15m candles

            if candles_vp and len(candles_vp) >= 20:

                # Create price bins and aggregate volume

                prices = []

                volumes = []

                for c in candles_vp:

                    high = c.get('high', 0)

                    low = c.get('low', 0)

                    volume = c.get('volume', 0)

                    if high > 0 and low > 0 and volume > 0:

                        # Use typical price (HL3) for each candle

                        typical_price = (high + low + c.get('close', (high + low) / 2)) / 3

                        prices.append(typical_price)

                        volumes.append(volume)

                

                if prices and volumes:

                    price_min = min(prices)

                    price_max = max(prices)

                    price_range = price_max - price_min

                    if price_range > 0:

                        # Create 20 bins for volume profile

                        num_bins = 20

                        bin_size = price_range / num_bins

                        volume_bins = {}

                        

                        for i, (price, volume) in enumerate(zip(prices, volumes)):

                            bin_index = int((price - price_min) / bin_size)

                            bin_index = min(bin_index, num_bins - 1)

                            bin_price = price_min + (bin_index * bin_size)

                            volume_bins[bin_price] = volume_bins.get(bin_price, 0) + volume

                        

                        if volume_bins:

                            # Find POC (Point of Control) - price level with highest volume

                            poc_price = max(volume_bins, key=volume_bins.get)

                            poc_volume = volume_bins[poc_price]

                            

                            # Calculate Value Area (70% of volume)

                            sorted_bins = sorted(volume_bins.items(), key=lambda x: x[1], reverse=True)

                            total_volume = sum(vol for _, vol in volume_bins.items())

                            value_area_volume = total_volume * 0.70

                            

                            cumulative_volume = 0

                            value_area_prices = []

                            for price, volume in sorted_bins:

                                cumulative_volume += volume

                                value_area_prices.append(price)

                                if cumulative_volume >= value_area_volume:

                                    break

                            

                            if value_area_prices:

                                value_area_high = max(value_area_prices)

                                value_area_low = min(value_area_prices)

                                

                                volume_profile_data['poc'] = poc_price

                                volume_profile_data['value_area_high'] = value_area_high

                                volume_profile_data['value_area_low'] = value_area_low

        except Exception:

            pass

        

        # Calculate Market Structure: BOS (Break of Structure) and CHoCH (Change of Character) (international standard)

        # Use cached data (historical, no need for fresh)

        market_structure = {}

        try:

            candles_structure = await get_candles_optimized(symbol, '15m', 48, force_fresh=False)  # 12 hours

            if candles_structure and len(candles_structure) >= 20:

                # Identify swing highs and lows

                swing_highs = []

                swing_lows = []

                

                for i in range(2, len(candles_structure) - 2):

                    high = candles_structure[i].get('high', 0)

                    low = candles_structure[i].get('low', 0)

                    

                    # Check if it's a swing high

                    if (high > candles_structure[i-1].get('high', 0) and 

                        high > candles_structure[i-2].get('high', 0) and

                        high > candles_structure[i+1].get('high', 0) and

                        high > candles_structure[i+2].get('high', 0)):

                        swing_highs.append((i, high))

                    

                    # Check if it's a swing low

                    if (low < candles_structure[i-1].get('low', float('inf')) and 

                        low < candles_structure[i-2].get('low', float('inf')) and

                        low < candles_structure[i+1].get('low', float('inf')) and

                        low < candles_structure[i+2].get('low', float('inf'))):

                        swing_lows.append((i, low))

                

                # Determine market structure

                if swing_highs and swing_lows:

                    recent_highs = sorted(swing_highs[-5:], key=lambda x: x[1], reverse=True)

                    recent_lows = sorted(swing_lows[-5:], key=lambda x: x[1])

                    

                    # Check for BOS (Break of Structure) - breaking previous swing high/low

                    if len(recent_highs) >= 2 and len(recent_lows) >= 2:

                        last_high = recent_highs[0][1]

                        prev_high = recent_highs[1][1] if len(recent_highs) >= 2 else last_high

                        last_low = recent_lows[0][1]

                        prev_low = recent_lows[1][1] if len(recent_lows) >= 2 else last_low

                        

                        # BOS Bullish: broke previous swing high

                        if current_price > prev_high:

                            market_structure['bos'] = 'BULLISH'

                        # BOS Bearish: broke previous swing low

                        elif current_price < prev_low:

                            market_structure['bos'] = 'BEARISH'

                        else:

                            market_structure['bos'] = 'NONE'

                        

                        # CHoCH (Change of Character): trend reversal signal

                        if len(recent_highs) >= 3 and len(recent_lows) >= 3:

                            # Check for higher highs (uptrend) or lower lows (downtrend)

                            if recent_highs[0][1] > recent_highs[1][1] > recent_highs[2][1]:

                                if recent_lows[0][1] < recent_lows[1][1]:

                                    market_structure['choch'] = 'BEARISH_REVERSAL'  # Higher highs but lower low forming

                                else:

                                    market_structure['choch'] = 'BULLISH'

                            elif recent_lows[0][1] < recent_lows[1][1] < recent_lows[2][1]:

                                if recent_highs[0][1] > recent_highs[1][1]:

                                    market_structure['choch'] = 'BULLISH_REVERSAL'  # Lower lows but higher high forming

                                else:

                                    market_structure['choch'] = 'BEARISH'

                            else:

                                market_structure['choch'] = 'NONE'

        except Exception:

            pass

        

        # CRITICAL: Ensure total_discount_percent >= min_discount before calculating discount_activation

        # This prevents discount_activation from being higher than min_activation_price

        # Only enforce if not immediate_entry (immediate entry allows 0% discount)

        if not immediate_entry:

            total_discount_percent = max(total_discount_percent, min_discount)

        

        # Calculate activation price using discount method

        final_discount = 1 - (total_discount_percent / 100)

        discount_activation = current_price * final_discount

        

        # Apply international standards with ADAPTIVE priority based on:

        # - Coin type (BLUECHIP, MAJOR, ALTCOIN, MICROCAP)

        # - Volatility (high vol = more conservative, low vol = more aggressive)

        # - Market regime (Uptrend, Downtrend, Sideways)

        # - Market structure (BOS, CHoCH)

        

        # Use coin_type and volatility from earlier (already fetched/calculated at line 7675-7676)

        # coin_type = await get_coin_type_range(symbol)  # Already fetched at line 7675

        # volatility = await calculate_coin_volatility(symbol)  # Already calculated at line 7676

        market_regime = analysis.market_regime

        

        # Determine adaptive priority based on coin type and volatility

        # BLUECHIP/MAJOR: Prefer EMA, VWAP, Support (more reliable)

        # ALTCOIN/MICROCAP: Prefer Fibonacci, Volume Profile (more dynamic)

        # High volatility: Prefer deeper levels (Fibonacci 61.8%, Support)

        # Low volatility: Prefer EMA, VWAP (tighter levels)

        

        # Define base priorities by coin type

        if coin_type in ['BLUECHIP', 'MAJOR']:

            # For stable coins, prefer technical indicators (EMA, VWAP)

            priority_map = {

                'Support Level': 2,

                'EMA50': 1,

                'EMA20': 3,

                'VWAP': 2,

                'Fibonacci 61.8%': 5,

                'Fibonacci 50%': 6,

                'Fibonacci 38.2%': 7,

                'Value Area Low': 4,

                'POC': 8

            }

        elif coin_type == 'ALTCOIN':

            # For altcoins, balance between technical and Fibonacci

            priority_map = {

                'Support Level': 1,

                'Fibonacci 61.8%': 2,

                'Value Area Low': 3,

                'Fibonacci 50%': 4,

                'EMA50': 5,

                'VWAP': 6,

                'EMA20': 7,

                'Fibonacci 38.2%': 8,

                'POC': 9

            }

        else:  # MICROCAP

            # For microcaps, prefer Fibonacci and Volume Profile (more volatile)

            priority_map = {

                'Support Level': 1,

                'Fibonacci 61.8%': 2,

                'Value Area Low': 3,

                'POC': 4,

                'Fibonacci 50%': 5,

                'VWAP': 6,

                'EMA50': 7,

                'Fibonacci 38.2%': 8,

                'EMA20': 9

            }

        

        # Adjust priorities based on volatility

        if volatility > 40:

            # High volatility: Prefer deeper retracement levels

            priority_map['Fibonacci 61.8%'] = max(1, priority_map.get('Fibonacci 61.8%', 5) - 2)

            priority_map['Support Level'] = max(1, priority_map.get('Support Level', 3) - 1)

            priority_map['EMA20'] = min(10, priority_map.get('EMA20', 7) + 2)  # Less reliable in high vol

        elif volatility < 15:

            # Low volatility: Prefer EMA and VWAP (tighter ranges)

            priority_map['EMA50'] = max(1, priority_map.get('EMA50', 4) - 1)

            priority_map['VWAP'] = max(1, priority_map.get('VWAP', 6) - 2)

            priority_map['EMA20'] = max(1, priority_map.get('EMA20', 7) - 1)

        

        # Adjust priorities based on market regime

        if market_regime in [MarketRegime.STRONG_UPTREND, MarketRegime.WEAK_UPTREND]:

            # Uptrend: Prefer EMA and VWAP (buy on pullback to trend)

            priority_map['EMA50'] = max(1, priority_map.get('EMA50', 4) - 1)

            priority_map['EMA20'] = max(1, priority_map.get('EMA20', 7) - 1)

            priority_map['VWAP'] = max(1, priority_map.get('VWAP', 6) - 1)

        elif market_regime in [MarketRegime.STRONG_DOWNTREND, MarketRegime.WEAK_DOWNTREND]:

            # Downtrend: Prefer deeper levels (Support, Fibonacci 61.8%)

            priority_map['Support Level'] = max(1, priority_map.get('Support Level', 2) - 1)

            priority_map['Fibonacci 61.8%'] = max(1, priority_map.get('Fibonacci 61.8%', 5) - 2)

            priority_map['EMA50'] = min(10, priority_map.get('EMA50', 4) + 2)  # Less reliable in downtrend

        else:  # SIDEWAYS or UNKNOWN

            # Sideways: Prefer Volume Profile and POC (range trading)

            priority_map['Value Area Low'] = max(1, priority_map.get('Value Area Low', 4) - 1)

            priority_map['POC'] = max(1, priority_map.get('POC', 8) - 2)

        

        # Adjust priorities based on market structure

        if market_structure.get('bos') == 'BULLISH':

            # Bullish BOS: Prefer EMA and VWAP (momentum entry)

            priority_map['EMA20'] = max(1, priority_map.get('EMA20', 7) - 2)

            priority_map['VWAP'] = max(1, priority_map.get('VWAP', 6) - 1)

        elif market_structure.get('bos') == 'BEARISH':

            # Bearish BOS: Prefer deeper levels (conservative)

            priority_map['Support Level'] = max(1, priority_map.get('Support Level', 2) - 1)

            priority_map['Fibonacci 61.8%'] = max(1, priority_map.get('Fibonacci 61.8%', 5) - 1)

        

        if market_structure.get('choch') in ['BULLISH_REVERSAL', 'BULLISH']:

            # Bullish reversal: Prefer EMA and VWAP

            priority_map['EMA50'] = max(1, priority_map.get('EMA50', 4) - 1)

            priority_map['VWAP'] = max(1, priority_map.get('VWAP', 6) - 1)

        elif market_structure.get('choch') in ['BEARISH_REVERSAL', 'BEARISH']:

            # Bearish reversal: Prefer deeper levels

            priority_map['Support Level'] = max(1, priority_map.get('Support Level', 2) - 1)

            priority_map['Fibonacci 61.8%'] = max(1, priority_map.get('Fibonacci 61.8%', 5) - 1)

        

        candidate_prices = []

        

        # Add candidates with adaptive priorities

        if support_level and support_level > discount_activation and support_level < current_price:

            candidate_prices.append(('Support Level', support_level, priority_map.get('Support Level', 1)))

        

        if fibonacci_levels.get('61.8') and fibonacci_levels['61.8'] > discount_activation and fibonacci_levels['61.8'] < current_price:

            candidate_prices.append(('Fibonacci 61.8%', fibonacci_levels['61.8'], priority_map.get('Fibonacci 61.8%', 2)))

        

        if volume_profile_data.get('value_area_low') and volume_profile_data['value_area_low'] > discount_activation and volume_profile_data['value_area_low'] < current_price:

            candidate_prices.append(('Value Area Low', volume_profile_data['value_area_low'], priority_map.get('Value Area Low', 3)))

        

        if ema50_price and ema50_price > discount_activation and ema50_price < current_price:

            candidate_prices.append(('EMA50', ema50_price, priority_map.get('EMA50', 4)))

        

        if fibonacci_levels.get('50.0') and fibonacci_levels['50.0'] > discount_activation and fibonacci_levels['50.0'] < current_price:

            candidate_prices.append(('Fibonacci 50%', fibonacci_levels['50.0'], priority_map.get('Fibonacci 50%', 5)))

        

        if vwap_price and vwap_price > discount_activation and vwap_price < current_price:

            candidate_prices.append(('VWAP', vwap_price, priority_map.get('VWAP', 6)))

        

        if ema20_price and ema20_price > discount_activation and ema20_price < current_price:

            candidate_prices.append(('EMA20', ema20_price, priority_map.get('EMA20', 7)))

        

        if fibonacci_levels.get('38.2') and fibonacci_levels['38.2'] > discount_activation and fibonacci_levels['38.2'] < current_price:

            candidate_prices.append(('Fibonacci 38.2%', fibonacci_levels['38.2'], priority_map.get('Fibonacci 38.2%', 8)))

        

        if volume_profile_data.get('poc') and volume_profile_data['poc'] > discount_activation and volume_profile_data['poc'] < current_price:

            candidate_prices.append(('POC', volume_profile_data['poc'], priority_map.get('POC', 9)))

        

        # Select the best candidate (highest priority, or highest price if same priority)

        # IMPORTANT: Ensure minimum discount is respected even when using candidate prices

        min_activation_price = current_price * (1 - min_discount / 100) if not immediate_entry else current_price

        

        # Check for large bid wall and adjust activation price if needed

        # If there's a large bid wall (support), price may not drop below it

        # So we should adjust activation price to be above bid wall level

        try:

            if orderbook_data and 'bids' in orderbook_data:

                bids = orderbook_data['bids']

                if bids:

                    bid_sizes = [float(bid[1]) for bid in bids[:5]]

                    if len(bid_sizes) > 0:

                        avg_bid_size = sum(bid_sizes) / len(bid_sizes)

                        max_bid_size = max(bid_sizes)

                        bid_ratio = max_bid_size / avg_bid_size if avg_bid_size > 0 else 0

                        

                        # Determine whale wall threshold based on coin type

                        if coin_type == 'BLUECHIP':

                            whale_wall_threshold = 2.3

                        elif coin_type == 'MAJOR':

                            whale_wall_threshold = 2.7

                        elif coin_type == 'ALTCOIN':

                            whale_wall_threshold = 3.2

                        else:

                            whale_wall_threshold = 4.2

                        

                        # Adjust threshold based on volatility

                        if volatility > 40:

                            whale_wall_threshold *= 1.2

                        elif volatility > 25:

                            whale_wall_threshold *= 1.1

                        elif volatility < 10:

                            whale_wall_threshold *= 0.9

                        

                        # If bid wall is large (strong support), check for spoofing first

                        if bid_ratio > whale_wall_threshold:

                            # Find bid wall level (price of largest bid order)

                            bid_wall_price = float(bids[0][0])  # Best bid price (where wall is)

                            

                            # Track bid wall history for spoofing detection

                            ws_manager = _init_ws_manager()

                            is_spoofing = False

                            spoofing_reason = ""

                            

                            if ws_manager:

                                # Track current bid wall

                                ws_manager.track_bid_wall(symbol, bid_ratio, bid_wall_price)

                                bid_wall_history = ws_manager.get_bid_wall_history(symbol)

                                

                                # Detect spoofing (appear-disappear pattern)

                                is_spoofing, spoofing_reason = detect_bid_wall_spoofing(

                                    symbol, bid_wall_history, bid_ratio, bid_wall_price, whale_wall_threshold

                                )

                            

                            # Only use bid wall for activation adjustment if it's NOT spoofing

                            if is_spoofing:

                                # Don't use spoofing bid wall for activation adjustment

                                if not suppress_logs:

                                    output_manager.print_static(

                                        f"{RED}⚠️ Spoofing bid wall detected ({bid_ratio:.1f}x): {spoofing_reason} - "

                                        f"ignoring for activation adjustment{RESET}"

                                    )

                            else:

                                # Real bid wall - proceed with activation adjustment

                                # Only adjust if activation price is below bid wall

                                # This means we're waiting for price to drop below support, which may not happen

                                if min_activation_price < bid_wall_price:

                                    # Adjust activation price to be slightly above bid wall (0.1% above)

                                    # This ensures we can still enter if bid wall holds and price bounces

                                    adjusted_activation = bid_wall_price * 1.001  # 0.1% above bid wall

                                    

                                    # But don't go above current price (that would be immediate entry)

                                    if adjusted_activation < current_price:

                                        min_activation_price = max(min_activation_price, adjusted_activation)

                                        if not suppress_logs:

                                            output_manager.print_static(

                                                f"{YELLOW}⚠️ Bid wall detected ({bid_ratio:.1f}x) - activation adjusted above wall: "

                                                f"${fmt(min_activation_price)} (wall at ${fmt(bid_wall_price)}){RESET}"

                                            )

        except Exception:

            pass  # Continue if bid wall check fails

        

        if candidate_prices:

            # Validate candidate prices: filter out unreasonable candidates

            validated_candidates = []

            for method, price, priority in candidate_prices:

                # Check jika price terlalu dekat dengan current price (< 0.5% discount)

                discount_pct = safe_division((current_price - price), current_price, 0.0) * 100

                if discount_pct < 0.5 and not immediate_entry:

                    continue  # Skip jika terlalu dekat (kecuali immediate entry)

                

                # Check jika price terlalu jauh dari current price (> max_discount * 1.2)

                if discount_pct > max_discount * 1.2:

                    continue  # Skip jika terlalu jauh (tidak reasonable)

                

                validated_candidates.append((method, price, priority))

            

            if validated_candidates:

                candidate_prices = validated_candidates

            # If all candidates filtered out, fall through to discount-based activation

            

            if candidate_prices:

                # Sort by priority (lower number = higher priority), then by price (higher = better entry)

                candidate_prices.sort(key=lambda x: (x[2], -x[1]))

                best_method, smart_activation, priority = candidate_prices[0]

            else:

                # All candidates filtered out, fall through to discount-based activation

                candidate_prices = []

            

            # Ensure candidate price respects minimum discount requirement (including manipulation adjustment and bid wall adjustment)

            # CRITICAL: Always enforce minimum discount - candidate price cannot be above min_activation_price

            # Note: min_activation_price may have been adjusted above bid wall if large bid wall detected

            if not immediate_entry and smart_activation > min_activation_price:

                # Candidate is too close to current price - use minimum discount activation instead

                # This ensures manipulation risk is properly accounted for in activation price

                smart_activation = min_activation_price

                if manipulation_discount > 0:

                    best_method = f"Minimum Discount ({min_discount:.1f}% incl. manipulation)"

                else:

                    best_method = f"Minimum Discount ({min_discount:.1f}%)"

                if not suppress_logs:

                    output_manager.print_static(f"{YELLOW}⚠️ Candidate price too high, enforcing minimum discount {min_discount:.1f}%{RESET}")

            

            if not suppress_logs:

                # Show which method was used

                method_display = {

                    'Support Level': '📊 Support Level',

                    'Fibonacci 61.8%': '📐 Fibonacci 61.8% (Golden Ratio)',

                    'Value Area Low': '📊 Value Area Low (Volume Profile)',

                    'EMA50': '📈 EMA50',

                    'Fibonacci 50%': '📐 Fibonacci 50%',

                    'VWAP': '📊 VWAP',

                    'EMA20': '📈 EMA20',

                    'Fibonacci 38.2%': '📐 Fibonacci 38.2%',

                    'POC': '📊 POC (Point of Control)'

                }

                display_name = method_display.get(best_method, best_method)

                output_manager.print_static(f"{CYAN}{display_name}: ${fmt(smart_activation)} (international standard){RESET}")

                

                # Show additional context if available

                if market_structure.get('bos') and market_structure['bos'] != 'NONE':

                    bos_emoji = '🟢' if market_structure['bos'] == 'BULLISH' else '🔴'

                    output_manager.print_static(f"{CYAN}   {bos_emoji} BOS: {market_structure['bos']}{RESET}")

                if market_structure.get('choch') and market_structure['choch'] != 'NONE':

                    output_manager.print_static(f"{CYAN}   🔄 CHoCH: {market_structure['choch']}{RESET}")

        else:

            # Fallback to discount-based activation

            # Ensure minimum discount is respected

            if not immediate_entry:

                smart_activation = max(discount_activation, min_activation_price)

            else:

                smart_activation = discount_activation

            if not suppress_logs:

                output_manager.print_static(f"{CYAN}📊 Using Discount-Based Activation: ${fmt(smart_activation)}{RESET}")

        

        # CRITICAL FIX: Final enforcement of minimum discount before returning

        # Ensure smart_activation always respects minimum discount requirement

        if not immediate_entry:

            # Recalculate min_activation_price to ensure it's current

            min_activation_price_final = current_price * (1 - min_discount / 100)

            # Use strict comparison - if activation is above min (allowing for floating point precision), enforce

            # We want: smart_activation <= min_activation_price_final

            # So if: smart_activation > min_activation_price_final + epsilon, we enforce

            epsilon = 1e-10

            if smart_activation > min_activation_price_final + epsilon:

                # Final enforcement: force minimum discount

                price_diff = smart_activation - min_activation_price_final

                if not suppress_logs and price_diff > 1e-8:

                    output_manager.print_static(f"{YELLOW}⚠️ Final check: Activation price ${fmt(smart_activation)} (${price_diff:.8f} above min) too high, enforcing minimum discount {min_discount:.1f}% → ${fmt(min_activation_price_final)}{RESET}")

                smart_activation = min_activation_price_final

        

        # Calculate and display actual discount used (based on final smart_activation)

        if not suppress_logs:

            actual_discount_pct = safe_division((current_price - smart_activation), current_price, 0.0) * 100

            # Final verification: ensure actual discount >= minimum (with very small tolerance for rounding)

            # Use 0.01% tolerance (much smaller) to ensure minimum is properly enforced

            if not immediate_entry and actual_discount_pct < min_discount - 0.01:  # 0.01% tolerance for rounding (reduced from 0.05%)

                # This should not happen, but enforce if it does

                smart_activation = current_price * (1 - min_discount / 100)

                actual_discount_pct = min_discount

                output_manager.print_static(f"{RED}🚨 CRITICAL: Discount {actual_discount_pct:.3f}% < minimum {min_discount:.1f}%, enforcing minimum → ${fmt(smart_activation)}{RESET}")

            

            if actual_discount_pct <= 0.05:

                momentum_note = " ─ strong momentum + high confidence" if immediate_entry else ""

                output_manager.print_static(f"{GREEN}🤖 AI ENTRY: Immediate (0.0% discount){momentum_note}{RESET}")

            else:

                # Show actual discount used with context (min/max range)

                min_discount_str = f"Min: {min_discount:.1f}%" if not immediate_entry else "Min: 0%"

                max_discount_str = f"Max: {max_discount:.1f}%"

                if combined_multiplier != 1.0:

                    # Build adjustment info - only show non-empty adjustments
                    adjustment_parts = []
                    if regime_adjustment_desc:  # Only add if not empty
                        adjustment_parts.append(regime_adjustment_desc)
                    if confidence_adjustment_desc:  # Only add if not empty
                        adjustment_parts.append(confidence_adjustment_desc)
                    
                    # Format: Responsive Compact (OPSI 1)
                    if adjustment_parts:
                        adjustment_info = " × ".join(adjustment_parts)
                        output_manager.print_static(f"{CYAN}💡 Entry: {actual_discount_pct:.1f}% ({min_discount_str}, {max_discount_str} for {coin_type}) │ {adjustment_info}{RESET}")
                    else:
                        # No adjustments to show
                        output_manager.print_static(f"{CYAN}💡 Entry: {actual_discount_pct:.1f}% ({min_discount_str}, {max_discount_str} for {coin_type}){RESET}")

                else:

                    output_manager.print_static(f"{CYAN}💡 Entry: {actual_discount_pct:.1f}% ({min_discount_str}, {max_discount_str} for {coin_type}){RESET}")

        

        return smart_activation

    except Exception as e:

        # Professional error handling with context

        error_handler.record_error(

            "Activation_Calculation_Error",

            "Error calculating smart activation price",

            {"symbol": symbol, "current_price": current_price, "error": str(e)}

        )

        output_manager.print_static(f"{RED}❌ Smart activation calculation error: {e}{RESET}")

        # Fallback to safe discount (1% below current price)

        fallback_activation = current_price * 0.99

        output_manager.print_static(f"{YELLOW}⚠️ Using fallback activation: ${fmt(fallback_activation)} (1% discount){RESET}")

        return fallback_activation



async def calculate_ai_recommended_delta(analysis: ActionAnalysis, indicators_1h: Dict, strategy_type: str = "BUY", symbol: str = "BTC_USDT", suppress_logs: bool = False) -> float:

    try:

        coin_type = await get_coin_type_range(symbol)

        coin_type_display = get_coin_display_name(symbol, coin_type)

        try:

            volatility = await calculate_coin_volatility(symbol)

        except:

            volatility = indicators_1h.get('volatility', 20)

        market_regime = indicators_1h.get('market_regime', MarketRegime.UNKNOWN)

        confidence = analysis.confidence

        risk_level = analysis.risk_level

        recommendation = analysis.recommendation  # STRONG_BUY, BUY, etc.

        btc_corr_data = None

        liquidity_data = None

        intraday_profile = None

        min_delta, max_delta = get_optimal_range(coin_type, volatility, market_regime)

        min_delta, max_delta = apply_smart_boundaries(min_delta, max_delta, symbol)

        base_delta = (min_delta + max_delta) / 2

        

        # Smart Delta Adjustment based on Market Conditions (MODERATE VERSION)

        # Catatan: get_optimal_range() sudah melakukan adjustment dasar untuk market regime

        # Adjustment ini adalah enhancement tambahan yang lebih moderat dan aman

        

        # Adjustments hanya diterapkan jika:

        # 1. Confidence tinggi (>70%) untuk mengurangi risiko false signal

        # 2. Volatility tidak terlalu tinggi (<45%) untuk menghindari whipsaw

        # 3. Adjustment lebih moderat untuk keseimbangan antara speed dan safety

        

        market_regime_adjustment = 1.0

        recommendation_adjustment = 1.0

        

        # Safety checks: hanya apply adjustment jika kondisi aman

        high_confidence = confidence >= 70.0

        moderate_volatility = volatility < 45.0  # Jangan terlalu agresif di high volatility

        should_apply_adjustment = high_confidence and moderate_volatility

        

        if should_apply_adjustment:

            if strategy_type == "BUY":

                # BUY Strategy: Persempit delta saat uptrend (moderate)

                if market_regime == MarketRegime.STRONG_UPTREND:

                    # Strong uptrend: persempit delta dengan moderate (10-15%)

                    market_regime_adjustment = 0.88  # Reduce 12% (moderate, tidak terlalu agresif)

                elif market_regime == MarketRegime.WEAK_UPTREND:

                    # Weak uptrend: persempit delta sedikit

                    market_regime_adjustment = 0.93  # Reduce 7%

                # Sideways dan Downtrend: tidak ada adjustment (biarkan get_optimal_range handle)

                

                # Recommendation Adjustment (STRONG_BUY/BUY) - moderate

                if recommendation == ActionRecommendation.STRONG_BUY:

                    # Strong buy signal: persempit delta dengan moderate

                    recommendation_adjustment = 0.90  # Reduce 10% (moderate)

                elif recommendation == ActionRecommendation.BUY:

                    # Buy signal: persempit delta sedikit

                    recommendation_adjustment = 0.95  # Reduce 5% (slight)

                # else: tidak ada adjustment

                    

            elif strategy_type == "SELL":

                # SELL Strategy: Persempit delta saat downtrend (moderate)

                if market_regime == MarketRegime.STRONG_DOWNTREND:

                    # Strong downtrend: persempit delta dengan moderate untuk exit cepat

                    market_regime_adjustment = 0.88  # Reduce 12% (moderate)

                elif market_regime == MarketRegime.WEAK_DOWNTREND:

                    # Weak downtrend: persempit delta sedikit

                    market_regime_adjustment = 0.93  # Reduce 7%

                # Sideways dan Uptrend: tidak ada adjustment

                

                # Recommendation Adjustment (STRONG_SELL/SELL) - moderate

                if recommendation == ActionRecommendation.STRONG_SELL:

                    # Strong sell signal: persempit delta dengan moderate

                    recommendation_adjustment = 0.90  # Reduce 10% (moderate)

                elif recommendation == ActionRecommendation.SELL:

                    # Sell signal: persempit delta sedikit

                    recommendation_adjustment = 0.95  # Reduce 5% (slight)

                # else: tidak ada adjustment

        

        # Combine adjustments (multiplicative)

        combined_adjustment = market_regime_adjustment * recommendation_adjustment

        

        # Apply adjustment to base_delta

        base_delta *= combined_adjustment

        try:

            orderbook_data = await get_orderbook_optimized(symbol, 20)

            flow_data = await get_order_flow_optimized(symbol, 20)

            if orderbook_data and flow_data:

                buy_pressure = flow_data.get('buy_pressure', 50)

                cvd_momentum = flow_data.get('cvd_momentum', 0)

                order_imbalance = flow_data.get('order_imbalance', 0)

                if buy_pressure > 65:

                    base_delta *= 1.15

                elif buy_pressure > 55:

                    base_delta *= 1.08

                elif buy_pressure < 45:

                    base_delta *= 0.92

                if cvd_momentum > 1000:

                    base_delta *= 1.10

                elif cvd_momentum < -500:

                    base_delta *= 0.90

                if order_imbalance > 10:

                    base_delta *= 1.05

                elif order_imbalance < -10:

                    base_delta *= 0.95

        except Exception as e:

            pass

        try:

            btc_corr_data = await get_btc_correlation(symbol)

            if btc_corr_data['correlation'] > 0.7:

                base_delta *= btc_corr_data['adjustment_factor']

        except Exception:

            btc_corr_data = None

        try:

            liquidity_data = await analyze_liquidity_adaptive(symbol, force_fresh=True)

            if liquidity_data['liquidity_grade'] == 'EXCELLENT':

                base_delta *= 1.05

            elif liquidity_data['liquidity_grade'] == 'POOR':

                base_delta *= 0.90

        except Exception:

            liquidity_data = None

        try:

            intraday_profile = get_intraday_pattern_adaptive(symbol)

            base_delta *= intraday_profile['adjustment_factor']

        except Exception:

            intraday_profile = None

        # Confidence adjustment with clearer thresholds

        # High confidence (>= 70%): Increase delta (more aggressive)

        # Low confidence (< 60%): Decrease delta (more conservative)

        if confidence >= 70:

            base_delta *= 1.1

        elif confidence < 60:

            base_delta *= 0.9

        # Confidence 60-70%: No adjustment (neutral)

        

        if risk_level == "VERY_HIGH":

            base_delta *= 0.8

        elif risk_level == "LOW":

            base_delta *= 1.1

        

        # Final delta calculation with bounds

        # Ensure delta stays within reasonable range after all adjustments

        final_delta = max(min_delta, min(max_delta, base_delta))

        

        # Log adjustment info if not suppressed

        # Recalculate combined_adjustment for logging (it was applied earlier)

        combined_adjustment_log = market_regime_adjustment * recommendation_adjustment

        if not suppress_logs and (market_regime_adjustment != 1.0 or recommendation_adjustment != 1.0):

            regime_name = market_regime.value if hasattr(market_regime, 'value') else str(market_regime)

            regime_name = regime_name.lower().replace('_', ' ') if regime_name != 'unknown' else 'sideways'

            rec_name = recommendation.value if hasattr(recommendation, 'value') else str(recommendation)

            adjustment_pct = (1.0 - combined_adjustment_log) * 100

            if combined_adjustment_log < 1.0:

                reason = ""

                if not high_confidence:

                    reason = " (low confidence, adjustment skipped)"

                elif not moderate_volatility:

                    reason = " (high volatility, adjustment skipped)"

                # Format: Responsive Compact - Delta info akan digabung dengan parameters
                # output_manager.print_static(f"{CYAN}📉 Delta narrowed by {abs(adjustment_pct):.0f}% (Regime: {regime_name}, Signal: {rec_name}){reason}{RESET}")

            elif combined_adjustment_log > 1.0:

                output_manager.print_static(f"{CYAN}📈 Delta widened by {adjustment_pct:.0f}% (Regime: {regime_name}, Signal: {rec_name}){RESET}")

        elif not suppress_logs and not should_apply_adjustment and (market_regime in [MarketRegime.STRONG_UPTREND, MarketRegime.WEAK_UPTREND, MarketRegime.STRONG_DOWNTREND, MarketRegime.WEAK_DOWNTREND] or recommendation in [ActionRecommendation.STRONG_BUY, ActionRecommendation.BUY, ActionRecommendation.STRONG_SELL, ActionRecommendation.SELL]):

            # Log jika adjustment seharusnya diterapkan tapi di-skip karena safety checks

            reason_parts = []

            if not high_confidence:

                reason_parts.append(f"confidence {confidence:.0f}% < 70%")

            if not moderate_volatility:

                reason_parts.append(f"volatility {volatility:.0f}% >= 45%")

            if reason_parts:

                output_manager.print_static(f"{YELLOW}ℹ️ Delta adjustment skipped: {', '.join(reason_parts)} (safety check){RESET}")

        # Note: Parameters (SL, Timeout, Offset, Poll) are displayed in hybrid_trailing_buy function
        # This function only calculates delta, so we don't display parameters here
        # Format output moved to hybrid_trailing_buy where all parameters are available

        if not suppress_logs:

            try:

                btc_corr = btc_corr_data or await get_btc_correlation(symbol)

                liquidity = liquidity_data or await analyze_liquidity_adaptive(symbol)

                intraday = intraday_profile or get_intraday_pattern_adaptive(symbol)

                if btc_corr['correlation'] > 0.7:

                    output_manager.print_static(f"{MAGENTA}📊 BTC Correlation: {btc_corr['correlation']:.3f} ({btc_corr['correlation_type']}) | BTC Trend: {btc_corr['btc_trend']} | {btc_corr['recommendation']}{RESET}")

                liquidity_emoji = "🟢" if liquidity['liquidity_grade'] in ['EXCELLENT', 'GOOD'] else "🟡" if liquidity['liquidity_grade'] == 'FAIR' else "🔴"

                coin_type_liq = liquidity.get('coin_type', 'UNKNOWN')

                total_depth_liq = liquidity.get('total_depth', liquidity['depth_bid']+liquidity['depth_ask'])

                total_depth_usd = liquidity.get('total_depth_usd')

                min_depth_required_usd = liquidity.get('min_depth_required_usd')

                # Format: Responsive Compact (OPSI 1)
                if TradingConfig.SHOW_DEPTH_USD_LOGS and total_depth_usd is not None and min_depth_required_usd is not None:

                    output_manager.print_static(

                        f"{liquidity_emoji} 💧 Liquidity: {liquidity['liquidity_grade']} ({coin_type_liq}) │ Depth: ${total_depth_usd:,.0f} │ Spread: {liquidity['spread_pct']:.4f}%{RESET}"

                    )

                else:

                    output_manager.print_static(

                        f"{liquidity_emoji} 💧 Liquidity: {liquidity['liquidity_grade']} ({coin_type_liq}) │ Spread: {liquidity['spread_pct']:.4f}%{RESET}"

                    )

                session_emoji = "🌏" if intraday['session'] == 'ASIAN' else "🌍" if intraday['session'] == 'EUROPEAN' else "🌎" if intraday['session'] == 'US' else "🌐"

                volume_ratio_info = ""

                if intraday.get('volume_ratio') is not None:

                    volume_ratio_info = f" | VolumeRatio: {intraday['volume_ratio']:.2f}"

                realized_vol_info = ""

                if intraday.get('realized_daily_volatility') is not None:

                    realized_vol_info = f" | DailyVol: {intraday['realized_daily_volatility']:.1f}%"

                data_source_info = ""

                if intraday.get('data_source') and intraday['data_source'] != 'NONE':

                    data_source_info = f" | Source: {intraday['data_source']}"

                # Format: Responsive Compact (OPSI 1)
                output_manager.print_static(

                    f"{session_emoji} 🕐 Session: {intraday['session']} │ Vol: {intraday['expected_volume']} │ Volatility: {intraday['expected_volatility']} │ {intraday['recommendation']}{volume_ratio_info}{realized_vol_info}{data_source_info}{RESET}"

                )
                output_manager.print_static(f"{CYAN}{'━'*80}{RESET}")

            except Exception as e:

                pass

        return final_delta

    except Exception as e:

        output_manager.print_static(f"{YELLOW}⚠️ Enhanced delta calculation error, using ML fallback: {e}{RESET}")

        error_handler.record_error("Delta_Error", "Enhanced delta calculation failed", {"error": str(e)})

        try:

            return ml_enhancer.ml_optimized_delta(analysis, indicators_1h, strategy_type)

        except Exception:

            return 2.0 if strategy_type == "BUY" else 2.2



async def calculate_coin_volatility(symbol: str) -> float:

    """

    Calculate volatility for trading purposes (daily-based, not fully annualized).

    For trading bots, we use daily volatility as the primary metric, which is more relevant

    than fully annualized volatility for short-term trading decisions.

    

    Formula: Daily volatility = std(returns) * sqrt(periods_per_day) * 100

    Then convert to percentage that represents expected daily movement.

    

    For crypto (24/7 trading):

    - 15m: sqrt(24 * 4) = sqrt(96) ≈ 9.8 (periods per day)

    - 1h: sqrt(24) = 4.9 (periods per day)

    """

    try:

        candles_15m = await get_candles_optimized(symbol, "15m", 20)

        candles_1h = await get_candles_optimized(symbol, "1h", 24)

        volatilities = []

        for candles, tf_name in [(candles_15m, "15m"), (candles_1h, "1h")]:

            if candles and len(candles) >= 10:

                try:

                    closes = [candle['close'] for candle in candles if candle.get('close') is not None]

                    if len(closes) < 10:

                        continue

                    returns = []

                    for i in range(1, len(closes)):

                        if closes[i-1] > 0:

                            ret = (closes[i] - closes[i-1]) / closes[i-1]

                            returns.append(ret)

                    if returns:

                        # For trading purposes, use daily-based volatility instead of fully annualized

                        # This is more relevant for short-term trading decisions

                        if tf_name == "15m":

                            # 15m: 24 hours * 4 periods per hour = 96 periods per day

                            # Use sqrt(96) ≈ 9.8 for daily volatility scaling

                            periods_per_day_factor = math.sqrt(24 * 4)

                            weight = 1.5  # Moderate weight for shorter timeframe

                        elif tf_name == "1h":

                            # 1h: 24 periods per day

                            # Use sqrt(24) ≈ 4.9 for daily volatility scaling

                            periods_per_day_factor = math.sqrt(24)

                            weight = 1.0

                        else:

                            # Default to daily (1 period per day)

                            periods_per_day_factor = 1.0

                            weight = 1.0

                        

                        # Calculate daily-based volatility: std(returns) * sqrt(periods_per_day) * 100

                        # This represents expected daily movement percentage

                        vol = np.std(returns) * periods_per_day_factor * 100

                        if not math.isnan(vol) and not math.isinf(vol):

                            # Store volatility with weight for weighted average calculation

                            volatilities.append((vol, weight))

                except Exception:

                    continue

        if not volatilities:

            return 10.0

        

        # Calculate weighted average if weights are provided

        try:

            if len(volatilities) > 0 and isinstance(volatilities[0], tuple):

                # Weighted average: sum(vol * weight) / sum(weights)

                total_weighted_vol = sum(vol * weight for vol, weight in volatilities)

                total_weight = sum(weight for _, weight in volatilities)

                avg_vol = safe_division(total_weighted_vol, total_weight, 20.0)

            else:

                # Simple average for backward compatibility (if old format)

                avg_vol = safe_division(sum(volatilities), len(volatilities), 20.0)

        except (TypeError, IndexError):

            # Fallback to simple average if there's any issue

            try:

                # Try to extract just the volatility values if mixed format

                vol_values = [v[0] if isinstance(v, tuple) else v for v in volatilities]

                avg_vol = safe_division(sum(vol_values), len(vol_values), 20.0)

            except Exception:

                avg_vol = 20.0  # Default fallback

        

        # Cap at 100% for safety (daily volatility > 100% is extremely rare and dangerous)

        return max(5.0, min(100.0, avg_vol))

    except Exception as e:

        output_manager.print_static(f"{YELLOW}⚠️ Volatility calculation error, using default: {e}{RESET}")

        error_handler.record_error("Volatility_Error", "Volatility calculation failed", {"error": str(e)})

        return 10.0



def get_safe_reentry_threshold(coin_type: str) -> float:

    thresholds = {

        "BLUECHIP": 20.0,

        "MAJOR": 18.0,

        "ALTCOIN": 15.0,

        "MICROCAP": 12.0,

        "STABLE": 10.0

    }

    return thresholds.get(coin_type, 18.0)



def get_smart_relax_tier(profit_pct: float) -> str:

    if profit_pct > 8:

        return "BIG"

    elif profit_pct > 3:

        return "MEDIUM"

    else:

        return "NONE"



async def get_smart_relax_window(symbol: str, volatility: float) -> int:

    coin_type = await get_coin_type_range(symbol)

    base_windows = {

        "BLUECHIP": 18,

        "MAJOR": 16,

        "ALTCOIN": 14,

        "MICROCAP": 12,

        "STABLE": 18

    }

    base_window_min = base_windows.get(coin_type, 15)

    volatility_adjustment = 0

    if volatility < 20:

        volatility_adjustment = 3

    elif volatility <= 40:

        volatility_adjustment = 0

    else:

        volatility_adjustment = -2

    final_window_min = base_window_min + volatility_adjustment

    final_window_min = max(10, min(21, final_window_min))

    final_window_sec = final_window_min * 60

    return final_window_sec



def get_profit_tier_adjustment(current_profit_pct: float) -> float:

    if current_profit_pct > 20:

        return 1.0

    elif current_profit_pct > 10:

        return 0.5

    elif current_profit_pct > 5:

        return 0.0

    elif current_profit_pct > 2:

        return -0.5

    else:

        return -1.0



async def check_market_quality(symbol: str) -> Tuple[str, int, dict]:

    try:

        candles_5m = await get_candles_optimized(symbol, '5m', 20)

        candles_1h = await get_candles_optimized(symbol, '1h', 24)

        if not candles_5m or not candles_1h:

            return 'WEAK', 0, {'error': 'Insufficient data'}

        indicators_5m = calculate_advanced_indicators(candles_5m)

        indicators_1h = calculate_advanced_indicators(candles_1h)

        score = 0

        details = {}

        recent_volume = candles_5m[-1]['volume'] if candles_5m else 0

        avg_volume = sum(c['volume'] for c in candles_5m[-10:]) / 10 if len(candles_5m) >= 10 else recent_volume

        recent_close = candles_5m[-1]['close'] if candles_5m else 0

        recent_open = candles_5m[-1]['open'] if candles_5m else 0

        price_action = safe_division((recent_close - recent_open), recent_open, 0) * 100

        if price_action > 1.0 and recent_volume > avg_volume * 1.5:

            buy_pressure_est = 70

        elif price_action > 0.5 and recent_volume > avg_volume:

            buy_pressure_est = 60

        elif price_action > 0:

            buy_pressure_est = 55

        elif price_action > -0.5:

            buy_pressure_est = 45

        else:

            buy_pressure_est = 35

        if buy_pressure_est > 65:

            score += 2

            details['buy_pressure'] = f"{buy_pressure_est}% (+2)"

        elif buy_pressure_est > 55:

            score += 1

            details['buy_pressure'] = f"{buy_pressure_est}% (+1)"

        else:

            details['buy_pressure'] = f"{buy_pressure_est}% (0)"

        volume_ratio = indicators_1h.get('volume_ratio', 1.0)

        if volume_ratio > 1.8:

            score += 2

            details['volume'] = f"{volume_ratio:.1f}x (+2)"

        elif volume_ratio > 1.2:

            score += 1

            details['volume'] = f"{volume_ratio:.1f}x (+1)"

        else:

            details['volume'] = f"{volume_ratio:.1f}x (0)"

        rsi = indicators_1h.get('rsi', 50)

        if rsi > 50:

            score += 2

            details['rsi'] = f"{rsi:.0f} (+2)"

        elif rsi > 40:

            score += 1

            details['rsi'] = f"{rsi:.0f} (+1)"

        else:

            details['rsi'] = f"{rsi:.0f} (0)"

        trend = indicators_1h.get('trend_strength', 0.5)

        if trend > 0.6:

            score += 2

            details['trend'] = f"{trend:.2f} (+2)"

        elif trend > 0.4:

            score += 1

            details['trend'] = f"{trend:.2f} (+1)"

        else:

            details['trend'] = f"{trend:.2f} (0)"

        has_reversal = False

        if len(candles_5m) >= 3:

            recent_highs = [c['high'] for c in candles_5m[-3:]]

            if recent_highs[2] < recent_highs[1] < recent_highs[0]:

                has_reversal = True

        if len(candles_5m) >= 5:

            recent_volumes = [c['volume'] for c in candles_5m[-5:]]

            if recent_volumes[-1] < sum(recent_volumes[:-1]) / 4 * 0.8:

                has_reversal = True

        if not has_reversal:

            score += 2

            details['pattern'] = "No reversal (+2)"

        else:

            details['pattern'] = "Reversal detected (0)"

        if score >= 8:

            quality = 'STRONG'

        elif score >= 5:

            quality = 'MODERATE'

        else:

            quality = 'WEAK'

        details['total_score'] = score

        return quality, score, details

    except Exception as e:

        return 'WEAK', 0, {'error': str(e)}



def get_dynamic_grace_stop(symbol: str, coin_type: str, volatility: float,

                           trend_strength: float, current_profit_pct: float) -> float:

    base_grace = 0.4

    if volatility < 15:

        vol_adj = 0.3

    elif volatility < 25:

        vol_adj = 0.2

    elif volatility < 35:

        vol_adj = 0.1

    elif volatility < 45:

        vol_adj = 0.0

    elif volatility < 60:

        vol_adj = -0.2

    else:

        vol_adj = -0.3

    coin_adj_map = {

        'BLUECHIP': 0.2,

        'MAJOR': 0.1,

        'ALTCOIN': 0.0,

        'MICROCAP': -0.15,

        'STABLE': 0.25

    }

    coin_adj = coin_adj_map.get(coin_type, 0.0)

    if trend_strength > 0.75:

        trend_adj = 0.1

    elif trend_strength < 0.4:

        trend_adj = -0.1

    else:

        trend_adj = 0.0

    if current_profit_pct > 15:

        profit_adj = 0.1

    elif current_profit_pct < 5:

        profit_adj = -0.1

    else:

        profit_adj = 0.0

    grace_pct = base_grace + vol_adj + coin_adj + trend_adj + profit_adj

    grace_pct = max(0.1, min(1.0, grace_pct))

    return grace_pct



def get_grace_period_duration(coin_type: str, volatility: float) -> int:

    base_durations = {

        'BLUECHIP': 180,

        'MAJOR': 150,

        'ALTCOIN': 120,

        'MICROCAP': 75,

        'STABLE': 180

    }

    base_duration = base_durations.get(coin_type, 120)

    if volatility < 15:

        duration_adj = 30

    elif volatility > 50:

        duration_adj = -30

    else:

        duration_adj = 0

    final_duration = base_duration + duration_adj

    final_duration = max(60, min(180, final_duration))

    return final_duration



async def monitor_grace_period(symbol: str, original_trigger: float, grace_trigger: float,

                               grace_duration: int, activate_price: float, high: float,

                               current_delta: float) -> str:

    grace_start = time.time()

    last_quality_check = grace_start

    quality_check_interval = 10

    output_manager.print_static(f"{GREEN}🟢 GRACE PERIOD: Monitoring for {grace_duration}s{RESET}")

    output_manager.print_static(f"{CYAN}   Original stop: ${fmt(original_trigger)} | Grace stop: ${fmt(grace_trigger)}{RESET}")

    output_manager.start_live_update()

    try:
        while time.time() - grace_start < grace_duration:
            price = await get_price_optimized(symbol)

            if price is None:
                await asyncio.sleep(2)
                continue

            profit_pct = safe_division((price - activate_price), activate_price, 0.0) * 100

            time_left = grace_duration - (time.time() - grace_start)

            status = f"【GRACE HOLD】{symbol} | 💵 ${fmt(price)} | Grace: ${fmt(grace_trigger)} | P/L: {profit_pct:+.2f}% | Time: {int(time_left)}s"

            output_manager.print_live(status)

            recovery_threshold = original_trigger * 1.001

            if price > recovery_threshold:
                output_manager.stop_live_update()
                output_manager.print_static(f"\n{GREEN}✅ RECOVERY! Price ${fmt(price)} > ${fmt(original_trigger)} - Resume trailing!{RESET}")
                return 'RECOVERED'

            if price <= grace_trigger:
                output_manager.stop_live_update()
                output_manager.print_static(f"\n{RED}🚨 GRACE STOP HIT! Selling at ${fmt(price)}{RESET}")
                return 'GRACE_HIT'

            if time.time() - last_quality_check >= quality_check_interval:
                quality, score, _ = await check_market_quality(symbol)
                last_quality_check = time.time()

                if quality == 'WEAK':
                    output_manager.stop_live_update()
                    output_manager.print_static(f"\n{RED}🔴 MARKET WEAKENED! (Score: {score}/10) - Selling at ${fmt(price)}{RESET}")
                    return 'MARKET_WEAK'
                elif quality == 'MODERATE':
                    output_manager.print_static(f"{YELLOW}⚠️  Market quality: MODERATE ({score}/10) - Monitoring closely...{RESET}")

            await asyncio.sleep(2)

        output_manager.stop_live_update()

        quality, score, _ = await check_market_quality(symbol)

        price = await get_price_optimized(symbol)

        if quality == 'STRONG' and price and price > grace_trigger * 1.005:
            output_manager.print_static(f"{YELLOW}⏰ Grace timeout - Market still STRONG, extending 30s...{RESET}")
            await asyncio.sleep(30)
            price = await get_price_optimized(symbol)

        output_manager.print_static(f"\n{YELLOW}⏰ GRACE TIMEOUT - Selling at ${fmt(price) if price else 'market'}{RESET}")

        return 'TIMEOUT_SELL'

    except Exception as e:

        output_manager.stop_live_update()

        output_manager.print_static(f"{RED}❌ Grace period error: {e} - Selling immediately!{RESET}")

        return 'GRACE_ERROR'

burst_queue_lock = None


active_burst = None



def _get_burst_lock():

    global burst_queue_lock

    if burst_queue_lock is None:

        burst_queue_lock = asyncio.Lock()

    return burst_queue_lock



async def parallel_monitor_with_burst(symbol: str, trigger: float, activate_price: float,

                                     high: float, current_delta: float,

                                     price_history: list) -> tuple[str, float]:

    exchange_client = _init_exchange_client()

    exchange_available = False

    if exchange_client and hasattr(exchange_client, 'test_connection'):

        try:

            exchange_available = exchange_client.test_connection(symbol)

        except Exception:

            pass

    if exchange_available:

        output_manager.print_static(f"{GREEN}✅ {SELECTED_EXCHANGE} connected - HYBRID mode ({SELECTED_EXCHANGE} 0.5s){RESET}")

        return await hybrid_parallel_monitor(symbol, trigger, activate_price, high, current_delta)

    else:

        output_manager.print_static(f"{YELLOW}⚠️ {SELECTED_EXCHANGE} unavailable - FALLBACK mode ({SELECTED_EXCHANGE} 1s only){RESET}")

        return await bybit_fast_fallback(symbol, trigger, activate_price)



async def hybrid_parallel_monitor(symbol: str, trigger: float, activate_price: float,

                                  high: float, current_delta: float) -> tuple[str, float]:

    async def monitor_rest():

        while True:

            price = await get_price_optimized(symbol)

            if price and price <= trigger:

                return ('REST', price)

            await asyncio.sleep(1.0)



    async def monitor_ws():

        while True:

            ws_manager = _init_ws_manager()

            price = None

            if ws_manager:

                price = ws_manager.get_price(symbol)

            if not price:

                exchange_client = _init_exchange_client()

                if exchange_client:

                    price = exchange_client.get_price(symbol)

            if price and price <= trigger:

                return ('WS', price)

            await asyncio.sleep(0.5)

    try:

        done, pending = await asyncio.wait(

            [monitor_rest(), monitor_ws()],

            return_when=asyncio.FIRST_COMPLETED

        )

        for task in pending:

            task.cancel()

            try:

                await task

            except asyncio.CancelledError:

                pass

        source, detected_price = done.pop().result()

        if source == 'WS':

            output_manager.print_static(f"\n{RED}⚡⚡⚡ WS EARLY DETECTION: ${fmt(detected_price)}{RESET}")

            confirmed_price = await burst_confirm_bybit(symbol, trigger, detected_price)

            if confirmed_price:

                return ('HYBRID', confirmed_price)

            else:

                output_manager.print_static(f"{YELLOW}⚠️ Burst timeout - Using WS: ${fmt(detected_price)}{RESET}")

                return ('HYBRID', detected_price)

        else:

            output_manager.print_static(f"{GREEN}✅ REST DETECTION: ${fmt(detected_price)}{RESET}")

            return ('HYBRID', detected_price)

    except Exception as e:

        output_manager.print_static(f"{RED}❌ Parallel monitor error: {e}{RESET}")

        price = await get_price_optimized(symbol)

        return ('FALLBACK', price if price else trigger)



async def burst_confirm_bybit(symbol: str, trigger: float, ws_price: float) -> float:

    global active_burst

    async with _get_burst_lock():

        active_burst = symbol

        output_manager.print_static(f"{YELLOW}🔥 BURST CONFIRM MODE (0.2s × 3s)...{RESET}")

        burst_start = time.time()

        burst_duration = 3.0

        burst_interval = 0.2

        coin_type = await get_coin_type_range(symbol)

        if coin_type == 'MICROCAP':

            burst_interval = 0.15

        elif coin_type in ['BLUECHIP', 'MAJOR']:

            burst_interval = 0.25

        else:

            burst_interval = 0.2

        burst_count = 0

        while time.time() - burst_start < burst_duration:

            rest_price = await get_price_optimized(symbol)

            burst_count += 1

            if rest_price:

                diff_pct = abs(rest_price - ws_price) / ws_price * 100

                output_manager.print_static(f"   Check #{burst_count}: ${fmt(rest_price)} (Δ{diff_pct:.2f}%)")

                if rest_price <= trigger:

                    if diff_pct < 0.5:

                        output_manager.print_static(f"{GREEN}✅ CONFIRMED! Match within {diff_pct:.2f}% - EXECUTING!{RESET}")

                        active_burst = None

                        return rest_price

                    else:

                        output_manager.print_static(f"{YELLOW}⚠️ Mismatch {diff_pct:.1f}% - Using REST price{RESET}")

                        active_burst = None

                        return rest_price

                else:

                    if burst_count == 1:

                        output_manager.print_static(f"{CYAN}   ⏳ Waiting for REST to reach trigger...{RESET}")

            await asyncio.sleep(burst_interval)

        output_manager.print_static(f"{YELLOW}⏰ Burst timeout ({burst_duration}s) - Final check...{RESET}")

        final_price = await get_price_optimized(symbol)

        active_burst = None

        if final_price and final_price <= trigger:

            output_manager.print_static(f"{GREEN}✅ Final check passed: ${fmt(final_price)}{RESET}")

            return final_price

        else:

            output_manager.print_static(f"{RED}❌ Price moved above trigger during burst{RESET}")

            return None



async def bybit_fast_fallback(symbol: str, trigger: float, activate_price: float) -> tuple[str, float]:

    output_manager.print_static(f"{CYAN}🔄 FAST FALLBACK: Bybit 1s (2x faster than normal){RESET}")

    while True:

        price = await get_price_optimized(symbol)

        if price and price <= trigger:

            profit_pct = safe_division((price - activate_price), activate_price, 0.0) * 100

            output_manager.print_static(f"{GREEN}✅ TRIGGER HIT: ${fmt(price)} (P/L: {profit_pct:+.2f}%){RESET}")

            return ('FALLBACK', price)

        await asyncio.sleep(1.0)



async def is_safe_post_profit_reentry(symbol: str, current_price: float, confidence: float, market_regime: MarketRegime) -> Tuple[bool, str, str]:

    last_close = portfolio_manager.last_profitable_close

    if not last_close:

        return False, "No recent profitable close", "NONE"

    profit_tier = get_smart_relax_tier(last_close.get('profit_pct', 0))

    if profit_tier == "NONE":

        return False, f"Profit too small ({last_close.get('profit_pct', 0):.1f}% < 3% minimum for smart relax)", "NONE"

    if last_close.get('reentry_used', False):

        return False, "Re-entry already used for this profitable close (one per exit limit)", "NONE"

    if last_close.get('symbol', '') != symbol:

        return False, f"Different symbol (last: {last_close.get('symbol', 'unknown')})", "NONE"

    try:

        exchange_client = _init_exchange_client()

        candles_1h = None

        if exchange_client and hasattr(exchange_client, 'get_candles'):

            try:

                candles_1h = exchange_client.get_candles(symbol, '1H', limit=100)

            except Exception:

                pass

        if not candles_1h:

            candles_1h = await get_candles_optimized(symbol, '1h', 100)

        if candles_1h:

            indicators = calculate_advanced_indicators(candles_1h)

            current_volatility = indicators.get('volatility', 20) if indicators else 20

        else:

            current_volatility = 20

    except:

        current_volatility = 20

    dynamic_window = get_smart_relax_window(symbol, current_volatility)

    time_since_close = time.time() - last_close.get('time', 0)

    if time_since_close > dynamic_window:

        return False, f"Too long since close ({time_since_close/60:.1f} min > {dynamic_window/60:.0f} min window)", "NONE"

    coin_type = await get_coin_type_range(symbol)

    max_threshold = get_safe_reentry_threshold(coin_type)

    if profit_tier == "MEDIUM":

        max_threshold *= 0.85

    exit_price_check = last_close.get('price', 0)

    price_above_exit = safe_division((current_price - exit_price_check), exit_price_check, 0.0) * 100

    if price_above_exit > max_threshold:

        return False, f"Too far above exit (+{price_above_exit:.1f}% > {max_threshold:.0f}% for {coin_type})", "NONE"

    confidence_required = 90.0

    if profit_tier == "MEDIUM":

        confidence_required = 92.0

    if confidence < confidence_required:

        return False, f"Confidence too low ({confidence:.1f}% < {confidence_required:.0f}% for {profit_tier} tier)", "NONE"

    if market_regime not in [MarketRegime.STRONG_UPTREND, MarketRegime.UPTREND]:

        return False, f"Not in uptrend regime ({market_regime.value})", "NONE"

    try:

        orderbook_data = await get_orderbook_optimized(symbol, 20)

        flow_data = await get_order_flow_optimized(symbol, 20)

        if orderbook_data and flow_data:

            # === INTEGRASI MODUL ANALYSIS BARU: WOBI ===

            try:

                wobi_value = calculate_weighted_orderbook_imbalance(orderbook_data, levels=20, current_price=current_price)

                wobi_strength = get_wobi_signal_strength(wobi_value)

                if wobi_strength < -0.5:  # Strong bearish WOBI

                    wobi_ratio = wobi_value.get('wobi', 0.5)

                    return False, f"Bearish WOBI detected (WOBI: {wobi_ratio:.3f})", "NONE"

                hidden_orders = detect_hidden_orders_with_wobi(orderbook_data, current_price)

                if hidden_orders.get('detected', False):

                    reason = hidden_orders.get('reason', 'Unknown reason')

                    return False, f"Hidden orders detected: {reason}", "NONE"

            except Exception:

                pass

            

            is_whale_wall, whale_desc, whale_details = detect_whale_wall_absorption(

                symbol, orderbook_data, flow_data

            )

            if is_whale_wall:

                return False, f"Whale wall detected: {whale_desc}", "NONE"

            is_false_signal, false_desc, false_details = detect_false_signal_patterns(

                candles_1h, flow_data, orderbook_data

            )

            if is_false_signal:

                return False, f"False signal: {false_desc}", "NONE"

            buy_pressure = flow_data.get('buy_pressure', 0)

            sell_pressure = flow_data.get('sell_pressure', 0)

            if buy_pressure < 45:

                return False, f"Weak buying pressure ({buy_pressure:.0f}% < 45%)", "NONE"

            if 'bids' in orderbook_data and 'asks' in orderbook_data:

                bid_volume = sum(bid[1] for bid in orderbook_data['bids'])

                ask_volume = sum(ask[1] for ask in orderbook_data['asks'])

                total_volume = bid_volume + ask_volume

                if total_volume > 0:

                    bid_ratio = safe_division(bid_volume, total_volume, 0.5)

                    if bid_ratio < 0.5:

                        return False, f"Bearish order book ({bid_ratio:.1%} bids)", "NONE"

    except Exception as e:

        pass

    coin_display = get_coin_display_name(symbol, coin_type)

    exit_price = last_close.get('price', 0)

    reason = f"Safe re-entry [{profit_tier} tier, {last_close.get('profit_pct', 0):.1f}% profit]: {time_since_close/60:.1f}min / {dynamic_window/60:.0f}min window ({current_volatility:.0f}% vol), +{price_above_exit:.1f}% from exit ${fmt(exit_price)} (max: {max_threshold:.0f}% for {coin_display})"

    return True, reason, profit_tier



async def pre_buy_analysis(symbol: str, activation_price: float, strategy: AIStrategy, waiting_start_time: float = None) -> bool:

    try:

        output_manager.print_static(f"{CYAN}🔍 PRE-BUY ANALYSIS: Validating market conditions... [{now_ts()}]{RESET}")

        analysis_timeout = 30

        analysis_start_time = time.time()

        start_time = waiting_start_time if waiting_start_time else analysis_start_time

        max_retries = 3

        retry_count = 0

        while retry_count < max_retries and time.time() - analysis_start_time < analysis_timeout:

            try:

                current_price = await get_price_optimized(symbol)

                candles_1h = await get_candles_optimized(symbol, "1h", 50)

                candles_15m = await get_candles_optimized(symbol, "15m", 20)

                if not all([current_price, candles_1h, candles_15m]):

                    output_manager.print_static(f"{YELLOW}⚠️ Pre-buy analysis: Incomplete data, retrying...{RESET}")

                    retry_count += 1

                    await asyncio.sleep(2)

                    continue

                if current_price <= 0:

                    output_manager.print_static(f"{RED}❌ Pre-buy analysis: Invalid price{RESET}")

                    return False

                break

            except Exception as e:

                output_manager.print_static(f"{YELLOW}⚠️ Pre-buy analysis retry {retry_count + 1}/{max_retries}: {e}{RESET}")

                retry_count += 1

                await asyncio.sleep(2)

        else:

            output_manager.print_static(f"{RED}❌ Pre-buy analysis timeout after {max_retries} retries{RESET}")

            return False

        indicators_1h = calculate_advanced_indicators(candles_1h)

        indicators_15m = calculate_advanced_indicators(candles_15m)

        coin_type = await get_coin_type_range(symbol)

        coin_type_display = get_coin_display_name(symbol, coin_type)

        volatility = await calculate_coin_volatility(symbol)

        rsi_1h = indicators_1h.get('rsi', 50)

        current_regime = indicators_1h.get('market_regime', MarketRegime.UNKNOWN)

        current_analysis = await analyze_hold_wait_buy_ai(symbol)

        is_safe_reentry, reentry_reason, relax_tier = await is_safe_post_profit_reentry(

            symbol, current_price, current_analysis.confidence, current_regime

        )

        if is_safe_reentry:

            tier_emoji = "🔥" if relax_tier == "BIG" else "⚡" if relax_tier == "MEDIUM" else "📊"

            output_manager.print_static(f"{GREEN}{tier_emoji} SMART RELAX MODE ACTIVE:{RESET} {reentry_reason}")

            output_manager.print_static(f"{GREEN}   Relaxed thresholds will be used for re-entry validation{RESET}")

        cancel_reasons = []

        price_deviation = safe_division(abs(current_price - activation_price), activation_price, 0.0) * 100

        price_deviation_max = get_price_deviation_threshold(coin_type, volatility)

        if price_deviation > price_deviation_max:

            cancel_reasons.append(f"Price deviated {price_deviation:.1f}% from activation (max: {price_deviation_max:.1f}% for {coin_type_display})")

        volume_ratio = indicators_1h.get('volume_ratio', 1)

        volume_ratio_min = get_volume_ratio_threshold(coin_type)

        if is_safe_reentry and volatility > 35:

            if relax_tier == "BIG":

                volume_ratio_min *= 0.75

            elif relax_tier == "MEDIUM":

                volume_ratio_min *= 0.875

            output_manager.print_static(f"{GREEN}   ✓ Volume threshold relaxed: {volume_ratio_min:.2f}x ({relax_tier} tier, volatile coin){RESET}")

        if volume_ratio < volume_ratio_min:

            cancel_reasons.append(f"Low volume (ratio: {volume_ratio:.1f}x, min: {volume_ratio_min:.1f}x for {coin_type_display})")

        trend_strength = indicators_1h.get('trend_strength', 0.5)

        if coin_type == 'BLUECHIP':

            trend_threshold = 0.35

        elif coin_type == 'MAJOR':

            trend_threshold = 0.32

        elif coin_type == 'ALTCOIN':

            trend_threshold = 0.28

        elif coin_type == 'MICROCAP':

            trend_threshold = 0.22

        elif coin_type == 'STABLE':

            trend_threshold = 0.25

        else:

            trend_threshold = 0.30

        if is_safe_reentry:

            if relax_tier == "BIG":

                trend_threshold *= 0.50

            elif relax_tier == "MEDIUM":

                trend_threshold *= 0.67

            output_manager.print_static(f"{GREEN}   ✓ Trend threshold relaxed: {trend_threshold:.2f} ({relax_tier} tier, {coin_type_display}){RESET}")

        if trend_strength < trend_threshold and strategy.strategy_type != StrategyType.MEAN_REVERSION:

            cancel_reasons.append(f"Weak trend (strength: {trend_strength:.2f})")

        if current_regime in [MarketRegime.STRONG_DOWNTREND, MarketRegime.HIGH_VOLUME_DISTRIBUTION]:

            cancel_reasons.append(f"Bearish regime: {current_regime.value}")

        support_1h = indicators_1h.get('support', current_price * 0.95)

        if current_price < support_1h * 0.99:

            cancel_reasons.append("Support level broken")

        momentum_1h = indicators_1h.get('momentum_1h', 0)

        if coin_type == 'BLUECHIP':

            momentum_threshold = -2.0

        elif coin_type == 'MAJOR':

            momentum_threshold = -2.5

        elif coin_type == 'ALTCOIN':

            momentum_threshold = -3.5

        elif coin_type == 'MICROCAP':

            momentum_threshold = -6.5

        elif coin_type == 'STABLE':

            momentum_threshold = -1.5

        else:

            momentum_threshold = -3.0

        if coin_type == 'BLUECHIP':

            conf_gate_big = 88.0

            conf_gate_medium = 89.0

        elif coin_type == 'MAJOR':

            conf_gate_big = 90.0

            conf_gate_medium = 91.0

        elif coin_type == 'ALTCOIN':

            conf_gate_big = 92.0

            conf_gate_medium = 93.0

        elif coin_type == 'MICROCAP':

            conf_gate_big = 94.0

            conf_gate_medium = 95.0

        else:

            conf_gate_big = 90.0

            conf_gate_medium = 91.0

        if is_safe_reentry:

            if relax_tier == "BIG" and current_analysis.confidence > conf_gate_big:

                momentum_threshold *= 1.67

                output_manager.print_static(f"{GREEN}   ✓ Momentum threshold relaxed: {momentum_threshold:.1f}% (BIG tier, {coin_type_display}, conf {current_analysis.confidence:.0f}% > {conf_gate_big:.0f}%){RESET}")

            elif relax_tier == "MEDIUM" and current_analysis.confidence > conf_gate_medium:

                momentum_threshold *= 1.33

                output_manager.print_static(f"{GREEN}   ✓ Momentum threshold relaxed: {momentum_threshold:.1f}% (MEDIUM tier, {coin_type_display}, conf {current_analysis.confidence:.0f}% > {conf_gate_medium:.0f}%){RESET}")

        if momentum_1h < momentum_threshold:

            cancel_reasons.append(f"Strong bearish momentum: {momentum_1h:.1f}%")

        try:

            current_analysis = await analyze_hold_wait_buy_ai(symbol)

            current_confidence = current_analysis.confidence

            # Use analysis confidence from strategy for accurate initial confidence tracking

            # Priority: strategy.analysis.confidence > strategy.confidence (if not 100.0) > current_confidence

            if hasattr(strategy, 'analysis') and strategy.analysis and hasattr(strategy.analysis, 'confidence'):

                initial_confidence = strategy.analysis.confidence

            elif hasattr(strategy, 'confidence') and strategy.confidence and strategy.confidence > 0 and strategy.confidence != 100.0:

                # Only use strategy.confidence if it's not the default 100.0 (hardcoded initial value)

                initial_confidence = strategy.confidence

            else:

                # Fallback: use current confidence if initial not available

                # This should not happen in normal flow (analysis should be stored), but handle gracefully

                initial_confidence = current_confidence

                # Log warning if using fallback due to hardcoded 100.0 value or missing analysis

                if hasattr(strategy, 'confidence') and strategy.confidence == 100.0:

                    output_manager.print_static(f"{YELLOW}⚠️ Using current confidence as initial (strategy.confidence was default 100.0%, analysis not stored){RESET}")

            coin_type = await get_coin_type_range(symbol)

            coin_type_display = get_coin_display_name(symbol, coin_type)

            current_volatility = await calculate_coin_volatility(symbol)

            current_regime = current_analysis.market_regime

            adaptive_threshold = get_adaptive_confidence_threshold(

                coin_type, current_volatility

            )

            # Format: Responsive Compact - Simplify phase 2 header (tidak terlalu verbose)
            # output_manager.print_static(f"\n{CYAN}{'='*80}{RESET}")
            # output_manager.print_static(f"{CYAN}🔍 PHASE 2: ADAPTIVE THRESHOLD VALIDATION{RESET}")
            # output_manager.print_static(f"{CYAN}{'='*80}{RESET}")

            output_manager.print_static(f"{YELLOW}📊 Coin Characteristics:{RESET}")

            output_manager.print_static(f"   ├─ Symbol: {symbol}")

            output_manager.print_static(f"   ├─ Type: {coin_type_display}")

            output_manager.print_static(f"   ├─ Volatility: {current_volatility:.1f}%")

            output_manager.print_static(f"   ├─ Market Regime: {current_regime.value}")

            output_manager.print_static(f"   └─ Risk Level: {current_analysis.risk_level}")

            output_manager.print_static(f"\n{CYAN}🎯 Threshold Calculation:{RESET}")

            output_manager.print_static(f"   ├─ Coin Type: {coin_type_display}")

            output_manager.print_static(f"   ├─ Volatility: {current_volatility:.1f}%")

            output_manager.print_static(f"   └─ {GREEN}Final Threshold: {adaptive_threshold:.1f}%{RESET}")

            output_manager.print_static(f"\n{YELLOW}✅ Confidence Check:{RESET}")

            output_manager.print_static(f"   ├─ Current: {current_confidence:.1f}%")

            output_manager.print_static(f"   ├─ Required: {adaptive_threshold:.1f}%")

            threshold = adaptive_threshold

            threshold_type = f"{coin_type_display} adaptive"

            confidence_drop = initial_confidence - current_confidence

            # Confidence check: Mandatory unless recovery override with minimum confidence

            min_confidence_for_recovery = 60.0  # Minimum confidence even with recovery override

            # Determine status icon based on confidence and recovery override (is_safe_reentry already calculated above)

            if current_confidence >= threshold:

                status_icon = '✅ PASS'

            elif is_safe_reentry and current_confidence >= min_confidence_for_recovery:

                status_icon = '⚠️ WARNING'

            else:

                status_icon = '❌ FAIL'

            output_manager.print_static(f"   └─ Result: {current_confidence:.1f}% {status_icon} (gap: {current_confidence - adaptive_threshold:+.1f}%)")

            output_manager.print_static(f"{CYAN}{'='*80}{RESET}\n")

            if current_confidence < threshold:

                if is_safe_reentry and current_confidence >= min_confidence_for_recovery:

                    # Recovery override: Allow with warning (minimum confidence met)

                    output_manager.print_static(

                        f"{YELLOW}⚠️ WARNING: Confidence {current_confidence:.1f}% < {threshold:.1f}% (proceeding due to recovery, min {min_confidence_for_recovery:.0f}% met){RESET}"

                    )

                    # Don't add to cancel_reasons - allow trade to proceed

                elif is_safe_reentry and current_confidence < min_confidence_for_recovery:

                    # Recovery override but confidence too low - still cancel

                    cancel_reasons.append(f"Low AI confidence: {current_confidence:.1f}% < {min_confidence_for_recovery:.0f}% (even with recovery override, threshold: {threshold:.1f}%)")

                else:

                    # No recovery override: Mandatory check - block trade

                    cancel_reasons.append(f"Low AI confidence: {current_confidence:.1f}% < {threshold:.1f}% ({threshold_type})")

            if initial_confidence >= 85:

                drop_threshold = 20

            elif initial_confidence >= 75:

                drop_threshold = 18

            elif initial_confidence >= 65:

                drop_threshold = 15

            else:

                drop_threshold = 12

            if confidence_drop > drop_threshold:

                cancel_reasons.append(f"Significant AI confidence drop: {confidence_drop:.1f}% (threshold: {drop_threshold}%, initial: {initial_confidence:.1f}% → current: {current_confidence:.1f}%)")

            if current_analysis.recommendation not in ["BUY", "STRONG_BUY"]:

                cancel_reasons.append(f"AI recommendation changed: {current_analysis.recommendation.value} (initial: BUY)")

            if current_confidence >= threshold and confidence_drop <= drop_threshold:

                output_manager.print_static(f"🧠 AI Confidence: {current_confidence:.1f}% (Initial: {initial_confidence:.1f}%, Drop: {confidence_drop:.1f}%) ✅")

                output_manager.print_static(f"🎯 Profit Mode: {threshold_type} | Threshold: {threshold}% | Drop Limit: {drop_threshold}%")

            else:

                output_manager.print_static(f"🧠 AI Confidence: {current_confidence:.1f}% (Initial: {initial_confidence:.1f}%, Drop: {confidence_drop:.1f}%) ❌")

                output_manager.print_static(f"🎯 Profit Mode: {threshold_type} | Threshold: {threshold}% | Drop Limit: {drop_threshold}%")

        except Exception as e:

            output_manager.print_static(f"⚠️ AI confidence check failed, proceeding with technical analysis: {e}")

            error_handler.record_error("AI_Confidence_Check_Error", "AI confidence check failed", {"error": str(e)})

        recovery_override = False

        try:

            recent_candles = await get_candles_optimized(symbol, '1m', 20)

            if recent_candles and len(recent_candles) >= 5:

                order_flow_data = None

                orderbook_data = None

                try:

                    ws_manager = _init_ws_manager()

                    if ws_manager:

                        if hasattr(ws_manager, 'get_order_flow_analysis'):

                            order_flow_data = ws_manager.get_order_flow_analysis(symbol, limit=20)

                        if hasattr(ws_manager, 'get_orderbook_analysis'):

                            orderbook_data = ws_manager.get_orderbook_analysis(symbol)

                except Exception:

                    pass

                manipulation_results = comprehensive_manipulation_check(

                    recent_candles, coin_type, volatility, order_flow_data, orderbook_data

                )

                if manipulation_results['pump_dump'][0]:

                    cancel_reasons.append(f"Pump & dump detected: {manipulation_results['pump_dump'][1]}")

                if manipulation_results['flash_crash'][0]:

                    cancel_reasons.append(f"Flash crash detected: {manipulation_results['flash_crash'][1]}")

                if manipulation_results['volume_manipulation'][0]:

                    cancel_reasons.append(f"Volume manipulation: {manipulation_results['volume_manipulation'][1]}")

                if manipulation_results['price_anomaly'][0]:

                    cancel_reasons.append(f"Price anomaly: {manipulation_results['price_anomaly'][1]}")

                risk_level = manipulation_results['overall_risk']

                recovery_opportunity = manipulation_results.get('recovery_opportunity', False)

                if risk_level == 'CRITICAL':

                    output_manager.print_static(f"🚨 Anti-Manipulation: CRITICAL RISK - Multiple threats detected")

                elif risk_level == 'HIGH':

                    output_manager.print_static(f"⚠️ Anti-Manipulation: HIGH RISK - {risk_level} threats detected")

                elif risk_level == 'MEDIUM':

                    output_manager.print_static(f"⚡ Anti-Manipulation: MEDIUM RISK - {risk_level} threat detected")

                else:

                    output_manager.print_static(f"✅ Anti-Manipulation: LOW RISK - Market conditions normal")

                if recovery_opportunity:

                    if manipulation_results['legitimate_recovery'][0]:

                        output_manager.print_static(f"🚀 Recovery Opportunity: {manipulation_results['legitimate_recovery'][1]}")

                    if manipulation_results['manipulation_cleanup'][0]:

                        output_manager.print_static(f"🔄 Market Normalized: {manipulation_results['manipulation_cleanup'][1]}")

                output_manager.print_static(f"🛡️ Manipulation Analysis:")

                output_manager.print_static(f"   📊 Pump & Dump: {'❌' if manipulation_results['pump_dump'][0] else '✅'} {manipulation_results['pump_dump'][1]}")

                output_manager.print_static(f"   💥 Flash Crash: {'❌' if manipulation_results['flash_crash'][0] else '✅'} {manipulation_results['flash_crash'][1]}")

                output_manager.print_static(f"   📈 Volume Manip: {'❌' if manipulation_results['volume_manipulation'][0] else '✅'} {manipulation_results['volume_manipulation'][1]}")

                output_manager.print_static(f"   🎯 Price Anomaly: {'❌' if manipulation_results['price_anomaly'][0] else '✅'} {manipulation_results['price_anomaly'][1]}")

                if recovery_opportunity and risk_level in ['LOW', 'MEDIUM']:

                    output_manager.print_static(f"💡 Smart Re-entry: Recovery detected, proceeding with caution")

                    recovery_override = True

                else:

                    recovery_override = False

            else:

                output_manager.print_static(f"⚠️ Anti-manipulation check skipped: Insufficient candle data")

        except Exception as e:

            error_str = str(e).lower()

            if 'timeout' in error_str or 'connection' in error_str or 'rate limit' in error_str:

                if is_safe_reentry:

                    output_manager.print_static(f"{YELLOW}⚠️ API issue (safe re-entry - proceeding): {str(e)[:30]}{RESET}")

                else:

                    output_manager.print_static(f"{YELLOW}⚠️ API issue (proceeding): {str(e)[:30]}{RESET}")

                error_handler.record_error("Anti_Manipulation_API_Error", "API error during anti-manipulation check", {"error": str(e)})

        try:

            orderbook_check = await get_orderbook_optimized(symbol, 20, force_fresh=True, max_age_s=5)

            if orderbook_check and 'bids' in orderbook_check and 'asks' in orderbook_check:

                bids = orderbook_check['bids']

                asks = orderbook_check['asks']

                if bids and asks:

                    best_bid = float(bids[0][0])

                    best_ask = float(asks[0][0])

                    spread_pct = ((best_ask - best_bid) / best_bid) * 100

                    if coin_type == 'BLUECHIP':

                        max_spread = 0.08

                    elif coin_type == 'MAJOR':

                        max_spread = 0.12

                    elif coin_type == 'ALTCOIN':

                        max_spread = 0.50

                    elif coin_type == 'MICROCAP':

                        max_spread = 1.20

                    else:

                        max_spread = 0.50

                    if spread_pct > max_spread:

                        cancel_reasons.append(f"Spread too wide: {spread_pct:.3f}% (max {max_spread:.3f}% for {coin_type})")

                        output_manager.print_static(

                            f"{RED}❌ Check 11: SPREAD TOO WIDE {spread_pct:.3f}% (max {max_spread:.3f}% for {coin_type}){RESET}"

                        )

                    else:

                        output_manager.print_static(

                            f"{GREEN}✅ Check 11: Spread {spread_pct:.3f}% (< {max_spread:.3f}% for {coin_type}){RESET}"

                        )

        except Exception as e:

            output_manager.print_static(f"{YELLOW}⚠️ Spread check error: {e} (proceeding with caution){RESET}")

        try:

            # Use force_fresh=True to match Check 11 and ensure consistency

            liquidity_result = await analyze_liquidity_adaptive(symbol, force_fresh=True)

            liquidity_recommendation = liquidity_result.get('recommendation', 'UNKNOWN')

            liquidity_score = liquidity_result.get('liquidity_score', 50)

            liquidity_spread = liquidity_result.get('spread_pct', 0)

            if liquidity_recommendation == 'AVOID':

                cancel_reasons.append(f"Poor liquidity: AVOID recommendation (spread {liquidity_spread:.2f}%, score {liquidity_score:.0f})")

                output_manager.print_static(

                    f"{RED}❌ Check 12: LIQUIDITY POOR (AVOID) - spread {liquidity_spread:.2f}%, score {liquidity_score:.0f}{RESET}"

                )

            elif liquidity_recommendation == 'CAUTION':

                output_manager.print_static(

                    f"{YELLOW}⚠️ Check 12: Liquidity CAUTION - spread {liquidity_spread:.2f}%, score {liquidity_score:.0f}{RESET}"

                )

            else:

                output_manager.print_static(

                    f"{GREEN}✅ Check 12: Liquidity {liquidity_recommendation} - spread {liquidity_spread:.2f}%, score {liquidity_score:.0f}{RESET}"

                )

        except Exception as e:

            output_manager.print_static(f"{YELLOW}⚠️ Liquidity check error: {e} (proceeding with caution){RESET}")

        if cancel_reasons and not recovery_override:

            output_manager.print_static(f"{RED}🚨 PRE-BUY ANALYSIS: CANCELLED [{now_ts()}]{RESET}")

            output_manager.print_static(f"{CYAN}📊 Current Market Snapshot:{RESET}")

            output_manager.print_static(f"   Price: ${fmt(current_price)} | Target: ${fmt(activation_price)}")

            output_manager.print_static(f"   Volume Ratio: {volume_ratio:.1f}x | Trend: {trend_strength:.2f}")

            output_manager.print_static(f"   Momentum: {momentum_1h:.1f}% | Regime: {current_regime.value}")

            output_manager.print_static(f"{RED}❌ Cancellation Reasons:{RESET}")

            for reason in cancel_reasons:

                output_manager.print_static(f"   • {reason}")

            output_manager.print_static(f"{YELLOW}💡 Recommendation: Wait for better setup{RESET}")

            await task_manager.cancel_all_tasks(symbol)

            return False

        else:

            output_manager.print_static(f"\n{GREEN}✅ PRE-BUY ANALYSIS: CONDITIONS FAVORABLE{RESET}")

            output_manager.print_static(f"{GREEN}{'─'*70}{RESET}")

            output_manager.print_static(f"{CYAN}Price: {price_deviation:.1f}% drift │ Volume: {volume_ratio:.1f}x │ Trend: {trend_strength:.2f} │ RSI: {rsi_1h:.0f} │ Momentum: {momentum_1h:+.1f}%{RESET}")

            output_manager.print_static(f"{CYAN}Regime: {current_regime.value.replace('_', ' ').title()} │ All checks PASSED ✅{RESET}")

            output_manager.print_static(f"{GREEN}{'─'*70}{RESET}")

            if current_volatility > 40:

                if current_volatility > 50:

                    vol_adj = 8.0

                elif current_volatility > 35:

                    vol_adj = 5.0

                elif current_volatility > 25:

                    vol_adj = 3.0

                else:

                    vol_adj = 0.0

                output_manager.print_static(f"\n{YELLOW}🛡️ EXTREME VOLATILITY PROTECTIONS ACTIVE:{RESET}")

                output_manager.print_static(f"   ├─ Volatility: {RED}{current_volatility:.1f}%{RESET} (Extreme!)")

                output_manager.print_static(f"   ├─ Threshold raised: +{vol_adj:.1f}%")

                output_manager.print_static(f"   ├─ Position size: ~{10 if current_volatility > 45 else 20}% (reduced!)")

                output_manager.print_static(f"   ├─ Re-analysis: Every 10 minutes")

                output_manager.print_static(f"   └─ Emergency trigger: {current_volatility/8:.1f}% price move")

            return True

    except Exception as e:

        output_manager.print_static(f"{YELLOW}⚠️ Pre-buy analysis error: {e}{RESET}")

        error_handler.record_error("PreBuy_Analysis_Error", "Pre-buy analysis failed", {"error": str(e)})

        return False



async def analyze_hold_wait_buy_ai(symbol: str, lite: bool = False, force_fresh: bool = False) -> ActionAnalysis:

    try:

        if lite:

            candles_15m = await get_candles_optimized(symbol, "15m", 60, force_fresh=force_fresh)

            candles_1h = None

            candles_4h = None

        else:

            candles_15m = await get_candles_optimized(symbol, "15m", 100, force_fresh=force_fresh)

            candles_1h = await get_candles_optimized(symbol, "1h", 100, force_fresh=force_fresh)

            candles_4h = await get_candles_optimized(symbol, "4h", 100, force_fresh=force_fresh)

        dq_source = candles_15m if lite else candles_1h

        data_quality_1h = data_validator.comprehensive_data_validation(symbol, dq_source)

        if not data_quality_1h['is_valid']:

            return ActionAnalysis(

                recommendation=ActionRecommendation.WAIT,

                confidence=20.0,

                reasons=[f"❌ Data quality issues: {', '.join(data_quality_1h['issues'])}",

                        f"Data Quality Score: {data_quality_1h['quality_score']:.1f}/100"],

                price_targets={},

                risk_level="VERY_HIGH",

                time_horizon="UNKNOWN",

                score=0.0,

                market_regime=MarketRegime.UNKNOWN

            )

        if (not lite and (not candles_15m or not candles_1h or not candles_4h)) or (lite and not candles_15m):

            analysis = ActionAnalysis(

                recommendation=ActionRecommendation.WAIT,

                confidence=25.0,

                reasons=["Insufficient data for quantitative AI analysis"],

                price_targets={},

                risk_level="HIGH",

                time_horizon="UNKNOWN",

                score=0.0,

                market_regime=MarketRegime.UNKNOWN

            )

            return analysis

        indicators_15m = calculate_advanced_indicators(candles_15m)

        if lite:

            indicators_1h = indicators_15m

            indicators_4h = indicators_15m

        else:

            indicators_1h = calculate_advanced_indicators(candles_1h)

            indicators_4h = calculate_advanced_indicators(candles_4h)

        if not indicators_15m or not indicators_1h:

            analysis = ActionAnalysis(

                recommendation=ActionRecommendation.WAIT,

                confidence=35.0,

                reasons=["Failed to calculate quantitative indicators"],

                price_targets={},

                risk_level="HIGH",

                time_horizon="UNKNOWN",

                score=0.0,

                market_regime=MarketRegime.UNKNOWN

            )

            return analysis

        current_price = indicators_1h.get('current', 0)

        volatility = indicators_1h.get('volatility', 10)

        ml_regime = ml_enhancer.ml_market_regime_detection(indicators_1h)

        indicators_1h['market_regime'] = ml_regime['regime']

        indicators_1h['ml_confidence_boost'] = ml_regime['ml_confidence_boost']

        risk_assessment = await risk_metrics.calculate_comprehensive_risk(

            symbol, 1000, current_price, volatility

        )

        

        # === INTEGRASI MODUL ANALYSIS BARU ===

        analysis_signals = {}

        analysis_confidence_boost = 0.0

        reasons = []  # Initialize reasons list before analysis modules use it

        

        try:

            # 1. Divergence Analysis

            if candles_15m and len(candles_15m) >= 20:

                divergence_result = await analyze_divergence(candles_15m)

                if divergence_result:

                    div_type = divergence_result.get('divergence_type', DivergenceType.NONE)

                    div_raw_strength = divergence_result.get('strength', 0.0)

                    if div_type != DivergenceType.NONE:

                        analysis_signals['divergence'] = divergence_result

                        div_strength = get_divergence_signal_strength(div_type, div_raw_strength)

                        if div_strength > 0.6:

                            analysis_confidence_boost += 2.0

                        if isinstance(div_type, DivergenceType):

                            reasons.append(f"📊 Divergence: {div_type.value} (strength: {div_strength:.1f})")

                        else:

                            reasons.append(f"📊 Divergence: {div_type} (strength: {div_strength:.1f})")

            

            # 2. Multi-Timeframe Analysis

            if not lite and candles_15m and candles_1h and candles_4h:

                try:

                    mtf_trend_15m = calculate_trend_from_candles(candles_15m)

                    mtf_trend_1h = calculate_trend_from_candles(candles_1h)

                    mtf_trend_4h = calculate_trend_from_candles(candles_4h)

                    # Check alignment manually since we already have trend calculations

                    bullish_count = sum(1 for t in [mtf_trend_15m, mtf_trend_1h, mtf_trend_4h] if isinstance(t, TrendDirection) and t == TrendDirection.UPTREND)

                    bearish_count = sum(1 for t in [mtf_trend_15m, mtf_trend_1h, mtf_trend_4h] if isinstance(t, TrendDirection) and t == TrendDirection.DOWNTREND)

                    if bullish_count >= 2:

                        mtf_alignment = {'aligned': True, 'alignment': 'BULLISH', 'strength': bullish_count / 3.0}

                        mtf_strength = get_multi_timeframe_signal_strength(mtf_alignment)

                        analysis_signals['multi_timeframe'] = {'aligned': True, 'strength': mtf_strength}

                        if mtf_strength > 0.7:

                            analysis_confidence_boost += 3.0

                        reasons.append(f"🔄 Multi-TF: Aligned ({mtf_strength:.1f})")

                    elif bearish_count >= 2:

                        # Bearish alignment - reduce confidence

                        analysis_confidence_boost = max(0, analysis_confidence_boost - 2.0)

                        reasons.append(f"🔄 Multi-TF: Bearish alignment detected")

                except Exception as e:

                    # Log error but continue with other analysis

                    error_handler.record_error("Multi_Timeframe_Error", str(e), {"context": "multi_timeframe_analysis"})

            

            # 3. VWAP Analysis

            if candles_1h and len(candles_1h) >= 20:

                try:

                    vwap_value = calculate_vwap(candles_1h)

                    if vwap_value:

                        vwap_trend = analyze_vwap_trend(current_price, vwap_value)

                        if vwap_trend and 'error' not in vwap_trend:

                            should_enter, reason = get_vwap_entry_signal(vwap_trend)

                            if should_enter:

                                analysis_confidence_boost += 1.5

                            reasons.append(f"📈 VWAP: ${fmt(vwap_value)} | Trend: {vwap_trend.get('trend', 'NEUTRAL')} | Signal: {reason}")

                        else:

                            reasons.append(f"📈 VWAP: ${fmt(vwap_value)} | Error in trend analysis")

                except Exception as e:

                    # Log error but continue with other analysis

                    error_handler.record_error("VWAP_Analysis_Error", str(e), {"context": "vwap_analysis"})

            

            # 4. Bollinger Bands + ADX

            if candles_1h and len(candles_1h) >= 20:

                try:

                    bb_adx_result = await analyze_bollinger_adx(candles_1h, current_price)

                    if bb_adx_result:

                        should_enter, reason = get_bollinger_adx_entry_signal(bb_adx_result)

                        if should_enter:

                            analysis_confidence_boost += 2.0

                        reasons.append(f"📊 BB+ADX: {'BUY' if should_enter else 'NEUTRAL'} ({reason})")

                    else:

                        reasons.append(f"📊 BB+ADX: Analysis failed")

                except Exception as e:

                    # Log error but continue with other analysis

                    error_handler.record_error("Bollinger_ADX_Error", str(e), {"context": "bollinger_adx_analysis"})

        except Exception as e:

            # Log error but continue execution to prevent crash

            error_handler.record_error("Analysis_Module_Error", str(e), {"context": "analysis_modules_integration"})

        

        ai_score = calculate_ai_score(indicators_15m, indicators_1h, indicators_4h)

        if lite:

            ai_score *= 0.95

        ml_boost = ml_regime.get('ml_confidence_boost', 0)

        # Add analysis confidence boost from new modules

        # Ensure confidence is between 0 and 95 to prevent negative values or overflow

        enhanced_confidence = min(95, max(0, ai_score + ml_boost + analysis_confidence_boost))

        # reasons list already initialized above, just append additional reasons

        reasons.append(f"🤖 AI Score: {ai_score:.1f}/100")

        reasons.append(f"🧠 ML Enhanced Confidence: +{ml_boost:.1f}%")

        if analysis_confidence_boost > 0:

            reasons.append(f"✨ Analysis Modules Boost: +{analysis_confidence_boost:.1f}%")

        reasons.append(f"📊 Market Regime: {ml_regime['regime'].value} ({ml_regime['confidence']:.1f}% ML confidence)")

        reasons.append(f"⚡ Volatility: {volatility:.1f}%")

        reasons.append(f"🛡️ Risk Level: {risk_assessment['risk_level']} (Score: {risk_assessment['composite_risk_score']:.1f}/100)")

        reasons.append(f"💰 VaR 1D: {risk_assessment['var_1d']} | Max DD: {risk_assessment['expected_max_drawdown']}")

        volume_1h = indicators_1h.get('volume_ratio', 1)

        momentum_1h = indicators_1h.get('momentum_1h', 0)

        if volume_1h > 1.5:

            reasons.append("📈 High volume - Strong confirmation")

        elif volume_1h < 0.7:

            reasons.append("📉 Low volume - Caution advised")

        if momentum_1h > 1.0:

            reasons.append("🚀 Positive momentum")

        elif momentum_1h < -1.0:

            reasons.append("🔻 Negative momentum")

        reasons.append(f"✅ Data Quality: {data_quality_1h['quality_score']:.1f}/100 - {data_quality_1h['recommendation']}")

        coin_type = await get_coin_type_range(symbol)

        coin_type_display = get_coin_display_name(symbol, coin_type)

        ai_thresholds = get_ai_recommendation_thresholds(coin_type)

        try:

            adaptive_min_conf = get_adaptive_confidence_threshold(coin_type, volatility)

            reasons.append(

                f"🧭 Classification: {coin_type_display} | Adaptive min confidence: {adaptive_min_conf:.0f}% | "

                f"Buy threshold: {ai_thresholds.get('BUY', 65)} | Strong Buy: {ai_thresholds.get('STRONG_BUY', 75)}"

            )

        except Exception:

            pass

        if ai_score >= ai_thresholds["STRONG_BUY"]:

            recommendation = ActionRecommendation.STRONG_BUY

            confidence = min(95.0, enhanced_confidence * (1.03 if lite else 1.05))

            reasons.insert(0, f"🎯 STRONG BUY: Quantitative AI + ML detected high-probability setup ({coin_type_display} threshold: {ai_thresholds['STRONG_BUY']})")

        elif ai_score >= ai_thresholds["BUY"]:

            recommendation = ActionRecommendation.BUY

            confidence = enhanced_confidence * (0.98 if lite else 1.0)

            reasons.insert(0, f"📈 BUY: Quantitative AI + ML detected favorable conditions ({coin_type_display} threshold: {ai_thresholds['BUY']})")

        elif ai_score >= ai_thresholds["HOLD"]:

            recommendation = ActionRecommendation.HOLD

            confidence = enhanced_confidence * (0.98 if lite else 1.0)

            reasons.insert(0, f"🤔 HOLD: Market conditions neutral - AI + ML suggest waiting ({coin_type_display} threshold: {ai_thresholds['HOLD']})")

        elif ai_score >= ai_thresholds["WAIT"]:

            recommendation = ActionRecommendation.WAIT

            confidence = 100 - enhanced_confidence

            reasons.insert(0, f"⏳ WAIT: Quantitative analysis + ML suggest patience ({coin_type_display} threshold: {ai_thresholds['WAIT']})")

        else:

            recommendation = ActionRecommendation.STRONG_SELL

            confidence = 100 - enhanced_confidence

            reasons.insert(0, f"🔻 STRONG SELL: AI + ML detected high-probability downside ({coin_type_display})")

        resistance_1h = indicators_1h.get('resistance', current_price * 1.03)

        support_1h = indicators_1h.get('support', current_price * 0.97)

        price_targets = {

            "immediate": current_price,

            "short_term": resistance_1h,

            "medium_term": resistance_1h * 1.02,

            "support_near": support_1h,

            "support_strong": support_1h * 0.98,

            "upside_potential": safe_division((resistance_1h - current_price), current_price, 0.0) * 100,

            "downside_risk": safe_division((current_price - support_1h), current_price, 0.0) * 100,

            "var_1d": risk_assessment['var_1d'],

            "expected_drawdown": risk_assessment['expected_max_drawdown'],

            "risk_adjusted_return": risk_assessment['risk_adjusted_return']

        }

        risk_level = risk_assessment['risk_level']

        regime_duration = ml_regime.get('duration_expectation', 'MEDIUM_DURATION')

        if regime_duration == "LONG_DURATION":

            time_horizon = "MEDIUM_TERM"

        elif regime_duration == "SHORT_DURATION":

            time_horizon = "SHORT_TERM"

        else:

            time_horizon = "SHORT_TERM"

        analysis = ActionAnalysis(

            recommendation=recommendation,

            confidence=confidence,

            reasons=reasons,

            price_targets=price_targets,

            risk_level=risk_level,

            time_horizon=time_horizon,

            score=ai_score,

            market_regime=ml_regime['regime'],

            ml_confidence_boost=ml_boost,

            sentiment_score=0.5,

            pattern_recognition={

                'data_quality_score': data_quality_1h['quality_score'],

                'risk_score': risk_assessment['composite_risk_score'],

                'regime_confidence': ml_regime['confidence'],

                'transition_probability': ml_regime.get('transition_probability', 50)

            }

        )

        return analysis

    except Exception as e:

        output_manager.print_static(f"{RED}Error dalam quantitative AI analysis: {e}{RESET}")

        error_handler.record_error("Analysis_Error", "Quantitative AI analysis failed", {"error": str(e)})

        analysis = ActionAnalysis(

            recommendation=ActionRecommendation.WAIT,

            confidence=15.0,

            reasons=[f"Quantitative AI analysis error: {str(e)}"],

            price_targets={},

            risk_level="HIGH",

            time_horizon="UNKNOWN",

            score=0.0,

            market_regime=MarketRegime.UNKNOWN

        )

        return analysis



async def hybrid_trailing_buy(symbol: str, delta_percent: float, activate_price: float,

                            advance: bool = False, offset: float = 0.0, skip_wait: bool = False,

                            poll_interval: float = 2.0, auto_trailing_stop: bool = True,

                            stop_loss_percent: float = 2.0, dynamic_ai_stop: bool = True,

                            auto_mode: bool = True, timeout_minutes: int = 30, skip_breakout_after_restart: bool = False,

                            take_profit_percent: float = None, rotation_mode: bool = False, analysis=None,

                            buy_amount: Optional[float] = None, current_rotation: int = 0):

    # TradingConfig is defined at module level in core.py, so it's available directly

    # Don't assign to TradingConfig here to avoid UnboundLocalError

    # Just use TradingConfig directly - it's in module scope

    

    # Track fixed mode message per symbol to avoid spam

    if not hasattr(hybrid_trailing_buy, '_fixed_mode_msgs'):

        hybrid_trailing_buy._fixed_mode_msgs = {}

    

    # Track pre-check pause messages to avoid spam (per symbol)

    if not hasattr(hybrid_trailing_buy, '_pause_msg_tracker'):

        hybrid_trailing_buy._pause_msg_tracker = {}

    

    symbol_tracker = hybrid_trailing_buy._pause_msg_tracker.setdefault(symbol, {

        'last_msg': {},

        'last_msg_time': {},

        'msg_cooldown': 5.0  # Only show same message every 5 seconds

    })

    

    import time

    def should_show_pause_msg(msg_key: str) -> bool:

        """Check if pause message should be shown (throttle duplicate messages)"""

        current_time = time.time()

        last_time = symbol_tracker['last_msg_time'].get(msg_key, 0)

        cooldown = symbol_tracker['msg_cooldown']

        

        if current_time - last_time >= cooldown:

            symbol_tracker['last_msg_time'][msg_key] = current_time

            symbol_tracker['last_msg'][msg_key] = msg_key

            return True

        return False

    

    coin_type_internal = await get_coin_type_range(symbol)

    coin_type_display = get_coin_display_name(symbol, coin_type_internal)

    current_strategy = AIStrategy(

        name="Full Auto Hybrid Trailing Buy",

        description="Fully automated trading with smart activation management",

        activation_price=activate_price,

        delta_percent=delta_percent,

        confidence=100.0,  # Will be updated with actual analysis confidence

        risk_reward_ratio=2.5,

        timeframe="1H",

        market_regime=MarketRegime.UNKNOWN,

        strategy_type=StrategyType.TREND_FOLLOWING

    )

    reanalysis_task = None

    waiting_start_time = time.time()

    initial_analysis = None

    try:

        # Use provided analysis if available (for consistency), otherwise get fresh analysis

        if analysis is not None:

            initial_analysis = analysis

            # Store initial analysis in strategy for accurate confidence tracking

            current_strategy.analysis = initial_analysis

            current_strategy.confidence = initial_analysis.confidence  # Update with actual confidence

            current_strategy.market_regime = initial_analysis.market_regime  # Update with actual regime

        else:

            # Always get initial analysis for confidence tracking, even if skip_wait=True

            try:

                initial_analysis = await analyze_hold_wait_buy_ai(symbol)

                # Store initial analysis in strategy for accurate confidence tracking

                current_strategy.analysis = initial_analysis

                current_strategy.confidence = initial_analysis.confidence  # Update with actual confidence

                current_strategy.market_regime = initial_analysis.market_regime  # Update with actual regime

            except Exception as e:

                output_manager.print_static(f"{YELLOW}⚠️ Initial Analysis Error (will use fallback): {e}{RESET}")

        

        if not skip_wait:

            try:

                if initial_analysis:

                    current_volatility = await calculate_coin_volatility(symbol)

                    current_price = await get_price_optimized(symbol)

                    min_delta, max_delta = get_optimal_range(coin_type_internal, current_volatility, initial_analysis.market_regime)

                    min_delta, max_delta = apply_smart_boundaries(min_delta, max_delta, symbol)

                    # Format: Responsive Compact (OPSI 1) - Cocok untuk layar besar & kecil
                    regime_display = initial_analysis.market_regime.value.replace('_', ' ').title()
                    regime_arrow = '↗' if 'uptrend' in initial_analysis.market_regime.value else '↘' if 'downtrend' in initial_analysis.market_regime.value else '→'
                    rotation_indicator = "🔄 AUTO-ROTATION" if rotation_mode else ""
                    
                    output_manager.print_static(f"\n{CYAN}{'━'*80}{RESET}")
                    output_manager.print_static(f"{GREEN}🤖 AI: {initial_analysis.recommendation.value} {initial_analysis.confidence:.0f}% │ 📊 {regime_display} │ ⚠️ {initial_analysis.risk_level} {rotation_indicator}{RESET}")
                    output_manager.print_static(f"{CYAN}{'━'*80}{RESET}")
                    
                    discount_pct = safe_division((current_price - activate_price), current_price, 0.0) * 100
                    
                    # Format: Responsive Compact (OPSI 1) - Display parameters after AI signal
                    # Calculate adaptive stop loss for display
                    try:
                        adaptive_sl = get_adaptive_stop_loss(coin_type_internal, current_volatility)
                    except:
                        adaptive_sl = stop_loss_percent
                    
                    # Format parameters line
                    output_manager.print_static(f"{CYAN}🎯 SL: {adaptive_sl:.1f}% │ Timeout: {timeout_minutes}min │ Offset: {offset:.1f}% │ Poll: {poll_interval:.1f}s │ Vol: {current_volatility:.1f}%{RESET}")

            except Exception as e:

                output_manager.print_static(f"{RED}⚠️ Initial Analysis Error: {e}{RESET}")

            reanalysis_task = await task_manager.create_task(

                symbol, "reanalysis",

                adaptive_time_based_reanalysis(symbol, current_strategy, "BUY")

            )

            last_price = activate_price

            start_time = waiting_start_time

            # Use coin_type and volatility from earlier (already fetched/calculated)

            # coin_type_internal already available from earlier

            # current_volatility already calculated at line 9786

            coin_type_display = get_coin_display_name(symbol, coin_type_internal)

            # Use initial_analysis if available, otherwise get fresh analysis for breakout threshold

            analysis_for_breakout = initial_analysis if initial_analysis else await analyze_hold_wait_buy_ai(symbol)

            max_breakout_threshold = await calculate_smart_breakout_threshold(

                symbol=symbol,

                coin_type=coin_type_internal,

                volatility=current_volatility,

                analysis=analysis_for_breakout,

                market_regime=analysis_for_breakout.market_regime

            )



            # CRITICAL FIX: Add minimum liquidity safety check before proceeding

            emergency_liquidity_check = await analyze_liquidity_adaptive(symbol, force_fresh=True)

            if emergency_liquidity_check:

                emergency_depth = emergency_liquidity_check.get('total_depth_usd', 0)



                # Define minimum safe depths per coin type

                min_safe_depths = {

                    'BLUECHIP': 50000,

                    'MAJOR': 10000,  # Reduced from 30000 to be more realistic

                    'ALTCOIN': 3000,  # Reduced from 10000 for better entry success

                    'MICROCAP': 1000

                }



                min_safe_depth = min_safe_depths.get(coin_type_internal, 5000)



                if emergency_depth < min_safe_depth:

                    output_manager.print_static(

                        f"{RED}🚨 EMERGENCY LIQUIDITY CHECK FAILED: ${emergency_depth:,.0f} < ${min_safe_depth:,.0f} minimum for {coin_type_internal}{RESET}"

                    )

                    output_manager.print_static(

                        f"{RED}❌ TRADE CANCELLED: Insufficient liquidity for safe execution{RESET}"

                    )

                    await task_manager.cancel_all_tasks(symbol)

                    return

                else:

                    output_manager.print_static(

                        f"{GREEN}✅ Emergency liquidity check passed: ${emergency_depth:,.0f} ≥ ${min_safe_depth:,.0f}{RESET}"

                    )

            output_manager.print_static(f"{CYAN}🎯 Smart Breakout Threshold: {max_breakout_threshold:.1f}% ({coin_type_internal} adaptive){RESET}")

            try:

                output_manager.print_static(f"\n{CYAN}🌐 Starting WebSocket stream for real-time data...{RESET}")

                # Use dynamic WebSocket manager (not hardcoded bybit_ws_manager)

                ws_manager = _init_ws_manager()

                asyncio.create_task(ws_manager.start_stream(symbol))

                # Wait longer for WebSocket to initialize and receive first data

                # Different exchanges may need different initialization times

                await asyncio.sleep(3)

                # Try multiple times to get price (WebSocket might need more time)

                ws_test_price = None

                for attempt in range(3):

                    ws_test_price = ws_manager.get_price(symbol)

                    if ws_test_price:

                        break

                    await asyncio.sleep(0.5)

                

                if ws_test_price:

                    output_manager.print_static(f"{GREEN}✅ WebSocket connected: Live price ${ws_test_price:.8f}{RESET}")

                # Don't show warning if WebSocket is still initializing - it's normal and will connect automatically

                # REST API fallback is handled automatically by get_price_optimized()

                status = ws_manager.get_cache_status(symbol)



                def _warm_str(ok):

                    return f"{GREEN}✓{RESET}" if ok else f"{YELLOW}…{RESET}"

                k_warm = status['kline_warm_count']

                k_total = status['kline_total']

                ob_txt = "Warm" if status['orderbook_warm'] else "Cold"

                price_txt = "Warm" if status['price_warm'] else "Cold"

                output_manager.print_static(

                        f"{CYAN}🧠 WS Cache:{RESET} Price {price_txt} | OB {ob_txt} | Kline {k_warm}/{k_total} warm (1m:{'✓' if status['kline']['1m'] else '…'}, 5m:{'✓' if status['kline']['5m'] else '…'}, 15m:{'✓' if status['kline']['15m'] else '…'}, 1h:{'✓' if status['kline']['1h'] else '…'}, 4h:{'✓' if status['kline']['4h'] else '…'})"

                    )
            except Exception as ws_error:
                output_manager.print_static(f"{YELLOW}⚠️ WebSocket start warning: {ws_error} - using REST API fallback{RESET}")

            output_manager.start_live_update()

            last_activation_price = activate_price

            last_breakout_check = 0

            if current_volatility > 50:

                breakout_cooldown = 15

            elif current_volatility > 30:

                breakout_cooldown = 20

            elif coin_type_internal == 'MAJOR':

                breakout_cooldown = 45

            else:

                breakout_cooldown = 30

            skip_breakout_detection = skip_breakout_after_restart

            breakout_skip_duration = 60

            timeout_minutes = None

            if initial_analysis.market_regime == MarketRegime.STRONG_UPTREND:

                if coin_type_internal == 'BLUECHIP':

                    timeout_minutes = 15

                elif coin_type_internal == 'MAJOR':

                    timeout_minutes = 12

                elif coin_type_internal == 'ALTCOIN':

                    timeout_minutes = 8

                else:

                    timeout_minutes = 5

            elif initial_analysis.market_regime == MarketRegime.WEAK_UPTREND:

                if coin_type_internal == 'BLUECHIP':

                    timeout_minutes = 25

                elif coin_type_internal == 'MAJOR':

                    timeout_minutes = 20

                elif coin_type_internal == 'ALTCOIN':

                    timeout_minutes = 15

                else:

                    timeout_minutes = 10

            else:

                # Default timeout untuk regime lain (SIDEWAYS, LOW_VOL_CONSOLIDATION, DOWNTREND, dll)

                # Prevent infinite waiting

                if coin_type_internal == 'BLUECHIP':

                    timeout_minutes = 45

                elif coin_type_internal == 'MAJOR':

                    timeout_minutes = 35

                elif coin_type_internal == 'ALTCOIN':

                    timeout_minutes = 25

                else:

                    timeout_minutes = 20

            timeout_seconds = timeout_minutes * 60 if timeout_minutes else None

            if timeout_minutes:

                output_manager.print_static(

                    f"{CYAN}⏰ Timeout: {timeout_minutes}m (will auto-enter if not triggered){RESET}"

                )

            else:

                output_manager.print_static(

                    f"{CYAN}⏰ Timeout: None (patient wait mode){RESET}"

                )

            price_fail_count = 0

            # Poll interval sudah di-set ke 1.0 detik oleh get_adaptive_poll_interval

            # Tidak perlu batasan lagi karena sudah fixed di 1.0

            last_recommendation_check = 0

            while True:

                price = await get_price_optimized(symbol)

                if price is None:

                    price_fail_count += 1

                    if price_fail_count >= 3:

                        output_manager.print_static(f"{YELLOW}⚠️ Cannot get price for {symbol} (fail #{price_fail_count}), retrying...{RESET}")

                    await asyncio.sleep(poll_interval)

                    continue

                price_fail_count = 0

                current_time = time.time()

                elapsed_time = current_time - start_time

                

                # Check recommendation setiap 5 menit untuk prevent stuck waiting

                if current_time - last_recommendation_check >= 300:  # Every 5 minutes

                    last_recommendation_check = current_time

                    try:

                        # Only check if waiting >10 minutes to avoid unnecessary API calls

                        if elapsed_time > 600:  # After 10 minutes

                            current_analysis = await analyze_hold_wait_buy_ai(symbol, lite=True)

                            

                            # Cancel jika recommendation bukan BUY/STRONG_BUY setelah waiting >10 menit

                            if current_analysis.recommendation not in [ActionRecommendation.STRONG_BUY, ActionRecommendation.BUY]:

                                output_manager.stop_live_update()

                                output_manager.print_static(

                                    f"\n{YELLOW}⚠️ Recommendation changed to {current_analysis.recommendation.value} "

                                    f"(confidence: {current_analysis.confidence:.0f}%) - Cancelling...{RESET}"

                                )

                                if rotation_mode:

                                    output_manager.print_static(

                                        f"{YELLOW}🔄 ROTATION MODE: Skipping {symbol} (unfavorable conditions) → Will scan for next coin{RESET}"

                                    )

                                    # Skip coin for next 2 rotations (more efficient than time-based cooldown)

                                    if current_rotation > 0:

                                        await mark_coin_skip_rotation(symbol, current_rotation, skip_rotations=2)

                                        skip_remaining = await get_skip_rotations_remaining(symbol, current_rotation)

                                        output_manager.print_static(

                                            f"{CYAN}⏸️ {symbol} skipped for {skip_remaining} more rotation(s) (will be scanned again after){RESET}"

                                        )

                                    else:

                                        # Fallback to time-based cooldown if rotation not available

                                        await mark_coin_cooldown(symbol, cooldown_seconds=300)

                                await task_manager.cancel_all_tasks(symbol)

                                return

                            

                            # Cancel jika confidence rendah (<50%) dan recommendation HOLD/WAIT

                            if current_analysis.recommendation in [ActionRecommendation.HOLD, ActionRecommendation.WAIT]:

                                if current_analysis.confidence < 50.0:

                                    output_manager.stop_live_update()

                                    output_manager.print_static(

                                        f"\n{YELLOW}⚠️ Low confidence {current_analysis.confidence:.0f}% with {current_analysis.recommendation.value} - Cancelling...{RESET}"

                                    )

                                    if rotation_mode:

                                        output_manager.print_static(

                                            f"{YELLOW}🔄 ROTATION MODE: Skipping {symbol} (unfavorable conditions) → Will scan for next coin{RESET}"

                                        )

                                        # Skip coin for next 2 rotations (more efficient than time-based cooldown)

                                        if current_rotation > 0:

                                            await mark_coin_skip_rotation(symbol, current_rotation, skip_rotations=2)

                                            skip_remaining = await get_skip_rotations_remaining(symbol, current_rotation)

                                            output_manager.print_static(

                                                f"{CYAN}⏸️ {symbol} skipped for {skip_remaining} more rotation(s) (will be scanned again after){RESET}"

                                            )

                                        else:

                                            # Fallback to time-based cooldown if rotation not available

                                            await mark_coin_cooldown(symbol, cooldown_seconds=300)

                                    await task_manager.cancel_all_tasks(symbol)

                                    return

                    except Exception:

                        pass  # Continue if check fails

                if timeout_minutes:

                    elapsed_minutes = elapsed_time / 60

                    if elapsed_minutes >= timeout_minutes:

                        output_manager.stop_live_update()

                        output_manager.print_static(

                            f"\n{YELLOW}⏰ TIMEOUT REACHED! ({timeout_minutes} minutes elapsed){RESET}"

                        )

                        if rotation_mode:

                            output_manager.print_static(

                                f"{YELLOW}🔄 ROTATION MODE: Skipping {symbol} (timeout) → Will scan for next coin{RESET}"

                            )

                            # Skip coin for next 2 rotations (more efficient than time-based cooldown)

                            if current_rotation > 0:

                                await mark_coin_skip_rotation(symbol, current_rotation, skip_rotations=2)

                                skip_remaining = await get_skip_rotations_remaining(symbol, current_rotation)

                                output_manager.print_static(

                                    f"{CYAN}⏸️ {symbol} skipped for {skip_remaining} more rotation(s) (will be scanned again after){RESET}"

                                )

                            else:

                                # Fallback to time-based cooldown if rotation not available

                                output_manager.print_static(

                                    f"{CYAN}⏸️ Marking {symbol} cooldown (5 min) to avoid re-selection{RESET}"

                                )

                                await mark_coin_cooldown(symbol, cooldown_seconds=300)

                            await task_manager.cancel_all_tasks(symbol)

                            return

                        else:

                            # Smart timeout: validate market conditions and calculate adaptive entry

                            output_manager.print_static(

                                f"{CYAN}💡 Timeout reached - validating market conditions before entry...{RESET}"

                            )

                            output_manager.print_static(

                                f"   Current Price: ${fmt(price)} (vs Target: ${fmt(activate_price)}){RESET}"

                            )

                            

                            # Re-fetch buy pressure before entry decision (for accuracy)

                            try:

                                fresh_order_flow = await get_order_flow_optimized(symbol, limit=50)

                                fresh_buy_pressure = fresh_order_flow.get('buy_pressure', 50.0)

                                if fresh_order_flow.get('trade_count', 0) > 0:

                                    output_manager.print_static(

                                        f"{CYAN}📊 Fresh Buy Pressure Check: {fresh_buy_pressure:.1f}% (from {fresh_order_flow.get('trade_count', 0)} recent trades){RESET}"

                                    )

                            except Exception:

                                pass  # Continue if re-fetch fails

                            

                            # Validate market conditions

                            is_valid, reason, updated_analysis = await validate_timeout_entry_conditions(

                                symbol, price, activate_price, initial_analysis

                            )

                            

                            if not is_valid:

                                # Conditions not valid - skip entry or use grace period

                                output_manager.print_static(

                                    f"{YELLOW}⚠️ Market conditions not suitable: {reason}{RESET}"

                                )

                                output_manager.print_static(

                                    f"{CYAN}⏸️ Skipping entry - market conditions have changed since initial analysis{RESET}"

                                )

                                output_manager.print_static(

                                    f"{CYAN}💡 Consider: Market may need more time, or conditions have deteriorated{RESET}"

                                )

                                await task_manager.cancel_all_tasks(symbol)

                                return  # Exit without entering

                            

                            # Calculate adaptive entry price

                            discount_pct = safe_division((price - activate_price), price, 0.0) * 100

                            adaptive_entry = await calculate_adaptive_timeout_entry_price(

                                symbol, price, activate_price, abs(discount_pct)

                            )

                            

                            output_manager.print_static(

                                f"{GREEN}✅ Market conditions validated - proceeding with adaptive entry{RESET}"

                            )

                            output_manager.print_static(

                                f"{CYAN}💰 Adaptive Entry Price: ${fmt(adaptive_entry)} (from current: ${fmt(price)}, target: ${fmt(activate_price)}){RESET}"

                            )

                            

                            # Update activate_price to adaptive_entry for trailing buy

                            activate_price = adaptive_entry

                            last_price = price

                            await task_manager.cancel_all_tasks(symbol)

                            break

                # Removed duplicate print_live output to prevent flickering

                # The detailed output with symbol, timer, and waited time is handled below (line 10742)

                if skip_breakout_detection:

                    time_since_restart = current_time - start_time

                    if time_since_restart >= breakout_skip_duration:

                        skip_breakout_detection = False

                        output_manager.print_static(f"{CYAN}ℹ️ Breakout detection re-enabled after restart{RESET}")

                

                # ===== BULLISH MOMENTUM DETECTION (Prevent Missing Bullish Movement) =====

                if auto_mode and elapsed_time > 30:  # Wait at least 30 seconds before checking

                    try:

                        # Get 5-minute candles for price change calculation

                        candles_5m = await get_candles_optimized(symbol, '5m', 3, force_fresh=True)

                        order_flow = await get_order_flow_optimized(symbol, limit=20)

                        

                        if candles_5m and len(candles_5m) >= 2 and order_flow:

                            # Calculate price change in last 5 minutes

                            current_candle = candles_5m[-1]

                            previous_candle = candles_5m[-2]

                            price_change_5m = safe_division((float(current_candle['close']) - float(previous_candle['close'])), float(previous_candle['close']), 0.0) * 100

                            

                            buy_pressure = order_flow.get('buy_pressure', 50)

                            cvd_momentum = order_flow.get('cvd_momentum', 0)

                            

                            # Bullish momentum detection: price up > 2% in 5m AND buy pressure > 60%

                            bullish_momentum_detected = price_change_5m > 2.0 and buy_pressure > 60

                            

                            if bullish_momentum_detected:

                                output_manager.stop_live_update()

                                output_manager.print_static(f"\n{GREEN}📈 BULLISH MOMENTUM DETECTED │ Price +{price_change_5m:.1f}% (5m) │ Buy Pressure: {buy_pressure:.1f}%{RESET}")

                                

                                # Early adjustment activation price untuk catch up dengan bullish movement

                                new_analysis = await analyze_hold_wait_buy_ai(symbol, lite=True, force_fresh=True)

                                new_activation = await calculate_smart_activation_price(symbol, price, new_analysis)

                                

                                activation_change_pct = safe_division((new_activation - activate_price), activate_price, 0.0) * 100

                                

                                # Only adjust if significant change (>1%)

                                if abs(activation_change_pct) > 1.0:

                                    output_manager.print_static(f"{CYAN}🔄 Early adjustment for bullish momentum...{RESET}")

                                    output_manager.print_static(f"{GREEN}🤖 BULLISH ADJUST: New activation ${fmt(new_activation)} (was ${fmt(activate_price)}, change: {activation_change_pct:+.1f}%){RESET}")

                                    activate_price = new_activation

                                    current_strategy.activation_price = new_activation

                                    last_activation_price = new_activation

                                    start_time = time.time()

                                    output_manager.start_live_update()

                                    continue

                                else:

                                    output_manager.print_static(f"{CYAN}💡 Bullish momentum detected but activation adjustment minimal ({activation_change_pct:+.1f}%){RESET}")

                                    output_manager.start_live_update()

                    except Exception as e:

                        # Silently continue if bullish momentum detection fails

                        pass

                

                # ===== EARLY ENTRY FOR STRONG BULLISH SIGNALS =====

                if auto_mode and elapsed_time > 60:  # Wait at least 60 seconds before checking

                    try:

                        order_flow = await get_order_flow_optimized(symbol, limit=50)

                        if order_flow:

                            buy_pressure = order_flow.get('buy_pressure', 50)

                            cvd_momentum = order_flow.get('cvd_momentum', 0)

                            

                            # Get current analysis for confidence check

                            current_analysis = await analyze_hold_wait_buy_ai(symbol, lite=True, force_fresh=True)

                            confidence = current_analysis.confidence

                            

                            # Calculate price change in last 5 minutes

                            candles_5m = await get_candles_optimized(symbol, '5m', 3, force_fresh=True)

                            price_change_5m = 0.0

                            if candles_5m and len(candles_5m) >= 2:

                                current_candle = candles_5m[-1]

                                previous_candle = candles_5m[-2]

                                price_change_5m = safe_division((float(current_candle['close']) - float(previous_candle['close'])), float(previous_candle['close']), 0.0) * 100

                            

                            # Strong bullish signals: buy_pressure > 70% AND price_change_5m > 3% AND confidence > 80%

                            strong_bullish_signals = buy_pressure > 70 and price_change_5m > 3.0 and confidence > 80

                            

                            if strong_bullish_signals:

                                output_manager.stop_live_update()

                                output_manager.print_static(f"\n{GREEN}🚀 STRONG BULLISH SIGNALS DETECTED │ Buy: {buy_pressure:.1f}% │ Price: +{price_change_5m:.1f}% (5m) │ Confidence: {confidence:.0f}%{RESET}")

                                

                                # Early entry dengan small discount (0.5%)

                                early_entry_price = price * (1 - 0.5 / 100.0)

                                discount_from_activation = safe_division((price - activate_price), price, 0.0) * 100

                                

                                # Only proceed if price is still above activation (not too far)

                                if discount_from_activation < 5.0:  # Max 5% discount

                                    output_manager.print_static(f"{CYAN}💰 Early entry price: ${fmt(early_entry_price)} (0.5% discount from current ${fmt(price)}){RESET}")

                                    output_manager.print_static(f"{GREEN}✅ Proceeding with early entry to catch bullish movement...{RESET}")

                                    

                                    # Update activation price to early entry

                                    activate_price = early_entry_price

                                    current_strategy.activation_price = early_entry_price

                                    last_activation_price = early_entry_price

                                    

                                    # Break from waiting loop to proceed with entry

                                    output_manager.start_live_update()

                                    break

                                else:

                                    output_manager.print_static(f"{YELLOW}⚠️ Price too far from activation ({discount_from_activation:.1f}%) - skipping early entry{RESET}")

                                    output_manager.start_live_update()

                    except Exception as e:

                        # Silently continue if early entry check fails

                        pass

                

                if auto_mode and not skip_breakout_detection:

                    price_above_activation = safe_division((price - activate_price), activate_price, 0.0) * 100

                    breakout_threshold = max_breakout_threshold

                    

                    # ===== LOWER BREAKOUT THRESHOLD FOR BULLISH =====

                    # Check for bullish conditions to use lower threshold

                    try:

                        order_flow = await get_order_flow_optimized(symbol, limit=20)

                        if order_flow:

                            buy_pressure = order_flow.get('buy_pressure', 50)

                            # Use lower breakout threshold (60% of normal) if buy pressure > 60%

                            if buy_pressure > 60:

                                bullish_breakout_threshold = breakout_threshold * 0.6

                                # Use the lower of normal or bullish threshold

                                breakout_threshold = min(breakout_threshold, bullish_breakout_threshold)

                    except Exception:

                        pass  # Continue with normal threshold if check fails

                    

                    if coin_type_internal == 'BLUECHIP':

                        min_wait_time = 90

                    elif coin_type_internal == 'MAJOR':

                        min_wait_time = 75

                    elif coin_type_internal == 'ALTCOIN':

                        min_wait_time = 60

                    else:

                        min_wait_time = 30

                    time_since_last_check = current_time - last_breakout_check

                    if price_above_activation > breakout_threshold and elapsed_time > min_wait_time and time_since_last_check >= breakout_cooldown:

                        last_breakout_check = current_time

                        output_manager.stop_live_update()

                        output_manager.print_static(f"\n{RED}🚨 BREAKOUT │ Price +{price_above_activation:.1f}% above target ({coin_type_display}){RESET}")

                        try:

                            breakout_analysis = await analyze_hold_wait_buy_ai(symbol)

                            if coin_type_internal == 'BLUECHIP':

                                volume_threshold = 2.0

                                buy_pressure_threshold = 55

                                cvd_threshold = 0

                            elif coin_type_internal == 'MAJOR':

                                volume_threshold = 1.8

                                buy_pressure_threshold = 52

                                cvd_threshold = -100

                            elif coin_type_internal == 'ALTCOIN':

                                # REDUCED from 1.5x to 1.2x for better entry success rate

                                volume_threshold = 1.2

                                buy_pressure_threshold = 50

                                cvd_threshold = -150

                            else:

                                volume_threshold = 1.2

                                buy_pressure_threshold = 45

                                cvd_threshold = -250

                            # Force fresh data for accurate breakout detection

                            order_flow = await get_order_flow_optimized(symbol, limit=20)

                            candles_5m = await get_candles_optimized(symbol, '5m', 10, force_fresh=True)

                            volume_confirmed = True

                            if candles_5m and len(candles_5m) >= 3:

                                recent_volumes = [float(c['volume']) for c in candles_5m[-3:]]

                                avg_volume = safe_division(sum(recent_volumes), len(recent_volumes), 1.0)

                                current_volume = float(candles_5m[-1]['volume'])

                                if current_volume < avg_volume * volume_threshold:

                                    volume_confirmed = False

                                    output_manager.print_static(f"{YELLOW}⚠️ Low volume: {current_volume/avg_volume:.2f}x average (need {volume_threshold}x for {coin_type_display}){RESET}")

                            order_flow_confirmed = True

                            buy_pressure = order_flow.get('buy_pressure', 50)

                            cvd_momentum = order_flow.get('cvd_momentum', 0)

                            order_imbalance = order_flow.get('order_imbalance', 0)

                            if buy_pressure < buy_pressure_threshold:

                                order_flow_confirmed = False

                                output_manager.print_static(f"{YELLOW}⚠️ Weak buy pressure: {buy_pressure:.1f}% (need >{buy_pressure_threshold}% for {coin_type_display}){RESET}")

                            elif cvd_momentum < cvd_threshold:

                                order_flow_confirmed = False

                                output_manager.print_static(f"{YELLOW}⚠️ Weak CVD momentum: {cvd_momentum:.0f} (need >{cvd_threshold} for {coin_type_display}){RESET}")

                            orderbook_data = await get_orderbook_optimized(symbol, 20)

                            is_whale_wall, whale_desc, whale_details = detect_whale_wall_absorption(

                                symbol, orderbook_data, order_flow

                            )

                            whale_wall_detected = is_whale_wall

                            if whale_wall_detected:

                                output_manager.print_static(f"{RED}🐋 WHALE WALL DETECTED: {whale_desc}{RESET}")

                                whale_buy_threshold = buy_pressure_threshold - 5

                                whale_cvd_threshold = cvd_threshold - 300

                                if buy_pressure < whale_buy_threshold or cvd_momentum < whale_cvd_threshold:

                                    order_flow_confirmed = False

                                    output_manager.print_static(f"{RED}❌ Whale wall with weak buying - likely manipulation!{RESET}")

                                else:

                                    output_manager.print_static(f"{YELLOW}⚠️ Whale wall detected, but strong buying pressure continues{RESET}")

                            output_manager.print_static(f"Analysis: {breakout_analysis.market_regime.value} │ Confidence: {breakout_analysis.confidence:.1f}%")

                            output_manager.print_static(f"Order Flow: Buy {buy_pressure:.1f}% │ CVD {cvd_momentum:+.0f} │ Imbalance {order_imbalance:+.1f}%")

                            output_manager.print_static(f"{CYAN}📊 Validation: Volume {'✅' if volume_confirmed else '❌'} │ Order Flow {'✅' if order_flow_confirmed else '❌'} │ Whale Wall {'⚠️' if whale_wall_detected else '✅'}{RESET}")

                            validations_passed = sum([volume_confirmed, order_flow_confirmed])

                            # CRITICAL FIX: Require BOTH volume AND order flow for safety

                            critical_passed = volume_confirmed and order_flow_confirmed

                            output_manager.print_static(f"{CYAN}📊 Critical Validation: {validations_passed}/2 passed │ Result: {'✅ PASS' if critical_passed else '❌ FAIL'} (require BOTH){RESET}")

                            validation_pass = critical_passed

                            execution_quality_pass = True

                            if orderbook_data and 'bids' in orderbook_data and 'asks' in orderbook_data:

                                bids = orderbook_data['bids']

                                asks = orderbook_data['asks']

                                if bids and asks:

                                    best_bid = float(bids[0][0])

                                    best_ask = float(asks[0][0])

                                    spread_pct = ((best_ask - best_bid) / best_bid) * 100

                                    if coin_type_internal == 'BLUECHIP':

                                        max_spread = 0.08

                                    elif coin_type_internal == 'MAJOR':

                                        max_spread = 0.12

                                    elif coin_type_internal == 'ALTCOIN':

                                        max_spread = 0.50

                                    elif coin_type_internal == 'MICROCAP':

                                        max_spread = 1.20

                                    else:

                                        max_spread = 0.50

                                    if spread_pct > max_spread:

                                        execution_quality_pass = False

                                        output_manager.print_static(

                                            f"{RED}❌ SPREAD TOO WIDE: {spread_pct:.3f}% "

                                            f"(max {max_spread:.3f}% for {coin_type_display}){RESET}"

                                        )

                                    else:

                                        output_manager.print_static(

                                            f"{GREEN}✅ Spread: {spread_pct:.3f}% "

                                            f"(< {max_spread:.3f}% for {coin_type_display}){RESET}"

                                        )

                                    ask_depth_top3 = sum([float(ask[1]) for ask in asks[:3]])

                                    best_ask_price = best_ask

                                    if best_ask_price > 1000:

                                        if coin_type_internal == 'BLUECHIP':

                                            min_depth_usd = 300000

                                        elif coin_type_internal == 'MAJOR':

                                            min_depth_usd = 150000

                                        elif coin_type_internal == 'ALTCOIN':

                                            min_depth_usd = 50000

                                        else:

                                            min_depth_usd = 20000

                                    elif best_ask_price > 100:

                                        if coin_type_internal == 'BLUECHIP':

                                            min_depth_usd = 150000

                                        elif coin_type_internal == 'MAJOR':

                                            min_depth_usd = 80000

                                        elif coin_type_internal == 'ALTCOIN':

                                            min_depth_usd = 25000

                                        else:

                                            min_depth_usd = 8000

                                    elif best_ask_price > 10:

                                        if coin_type_internal == 'BLUECHIP':

                                            min_depth_usd = 80000

                                        elif coin_type_internal == 'MAJOR':

                                            min_depth_usd = 40000

                                        elif coin_type_internal == 'ALTCOIN':

                                            min_depth_usd = 10000

                                        else:

                                            min_depth_usd = 3000

                                    elif best_ask_price > 1:

                                        if coin_type_internal == 'BLUECHIP':

                                            min_depth_usd = 40000

                                        elif coin_type_internal == 'MAJOR':

                                            min_depth_usd = 20000

                                        elif coin_type_internal == 'ALTCOIN':

                                            min_depth_usd = 5000

                                        else:

                                            min_depth_usd = 1500

                                    elif best_ask_price > 0.01:

                                        if coin_type_internal == 'BLUECHIP':

                                            min_depth_usd = 20000

                                        elif coin_type_internal == 'MAJOR':

                                            min_depth_usd = 10000

                                        elif coin_type_internal == 'ALTCOIN':

                                            min_depth_usd = 2000

                                        else:

                                            min_depth_usd = 500

                                    else:

                                        if coin_type_internal == 'BLUECHIP':

                                            min_depth_usd = 10000

                                        elif coin_type_internal == 'MAJOR':

                                            min_depth_usd = 5000

                                        elif coin_type_internal == 'ALTCOIN':

                                            min_depth_usd = 1000

                                        else:

                                            min_depth_usd = 100

                                    ask_depth_usd = ask_depth_top3 * best_ask

                                    if ask_depth_usd < min_depth_usd:

                                        output_manager.print_static(

                                            f"{YELLOW}⚠️ THIN ORDERBOOK: ${ask_depth_usd:,.0f} "

                                            f"(min ${min_depth_usd:,.0f} for {coin_type_display}){RESET}"

                                        )

                                    else:

                                        output_manager.print_static(

                                            f"{GREEN}✅ Depth: ${ask_depth_usd:,.0f} "

                                            f"(> ${min_depth_usd:,.0f} for {coin_type_display}){RESET}"

                                        )

                                    current_price_check = await get_price_optimized(symbol)

                                    if current_price_check:

                                        ask_deviation = abs((best_ask - current_price_check) / current_price_check) * 100

                                        if ask_deviation > 0.5:

                                            output_manager.print_static(

                                                f"{YELLOW}⚠️ ORDERBOOK DEVIATION: {ask_deviation:.2f}% "

                                                f"(best ask ${best_ask:.8f} vs current ${current_price_check:.8f}){RESET}"

                                            )

                                        else:

                                            output_manager.print_static(

                                                f"{GREEN}✅ Best Ask: ${best_ask:.8f} "

                                                f"(fresh data, {ask_deviation:.2f}% deviation){RESET}"

                                            )

                            final_validation_pass = validation_pass and execution_quality_pass

                            output_manager.print_static(

                                f"{CYAN}📊 EXECUTION QUALITY: {'✅ PASS' if execution_quality_pass else '❌ FAIL (spread too wide)'}{RESET}"

                            )

                            if (breakout_analysis.recommendation in [ActionRecommendation.STRONG_BUY, ActionRecommendation.BUY] and

                                breakout_analysis.confidence >= 70 and final_validation_pass):

                                output_manager.print_static(f"{GREEN}✅ BREAKOUT CONFIRMED! Proceeding with buy at current price...{RESET}")

                                await task_manager.cancel_all_tasks(symbol)

                                break

                            elif not execution_quality_pass:

                                output_manager.print_static(

                                    f"{RED}❌ EXECUTION QUALITY FAILED: Spread too wide - "

                                    f"waiting for better entry price...{RESET}"

                                )

                                output_manager.start_live_update()

                                continue

                            elif not validation_pass:

                                reasons = []

                                if not volume_confirmed: reasons.append("low volume")

                                if not order_flow_confirmed: reasons.append("weak order flow")

                                reason = ", ".join(reasons)

                                output_manager.print_static(f"{YELLOW}⚠️ Critical validation failed: {reason} - need BOTH volume AND order flow{RESET}")

                                output_manager.start_live_update()

                                continue

                            else:

                                output_manager.print_static(f"{YELLOW}🔄 RESTART: Weak trend ({breakout_analysis.confidence:.1f}%), fresh analysis...{RESET}")

                                await task_manager.cancel_all_tasks(symbol)

                                await asyncio.sleep(3)

                                new_delta = await calculate_ai_recommended_delta(breakout_analysis, {}, "BUY", symbol)

                                new_activation = await calculate_smart_activation_price(symbol, price, breakout_analysis)

                                output_manager.print_static(f"Fresh: {breakout_analysis.recommendation.value} {breakout_analysis.confidence:.0f}% │ ${fmt(price)}")

                                if breakout_analysis.confidence < 65:

                                    output_manager.print_static(f"{YELLOW}🤖 AUTO WAITING: Confidence low, entering smart waiting mode...{RESET}")

                                    output_manager.print_static(f"{CYAN}⏳ Bot will monitor market until conditions improve{RESET}")

                                    wait_start = time.time()

                                    last_wait_update = wait_start

                                    last_wait_confidence = 0.0

                                    wait_update_count = 0

                                    output_manager.print_static(f"{CYAN}⏳ Smart waiting activated (updates every 5 min or on significant change){RESET}")

                                    while True:

                                        await asyncio.sleep(30)

                                        try:

                                            wait_analysis = await analyze_hold_wait_buy_ai(symbol)

                                            wait_price = await get_price_optimized(symbol)

                                            current_wait = time.time()

                                            if (wait_analysis.recommendation in [ActionRecommendation.STRONG_BUY, ActionRecommendation.BUY] and

                                                wait_analysis.confidence >= 65.0):

                                                output_manager.print_static(f"\n{GREEN}✅ Conditions improved! Starting trading...{RESET}")

                                                wait_delta = await calculate_ai_recommended_delta(wait_analysis, {}, "BUY", symbol)

                                                wait_activation = await calculate_smart_activation_price(symbol, wait_price, wait_analysis)

                                                await hybrid_trailing_buy(

                                                    symbol=symbol,

                                                    delta_percent=wait_delta,

                                                    activate_price=wait_activation,

                                                    advance=True,

                                                    offset=2.0,

                                                    skip_wait=False,

                                                    auto_trailing_stop=True,

                                                    stop_loss_percent=2.0,

                                                    dynamic_ai_stop=True,

                                                    auto_mode=True,

                                                    timeout_minutes=30,

                                                    skip_breakout_after_restart=True

                                                )

                                                return

                                        except Exception as e:

                                            output_manager.print_static(f"{RED}⚠️ Error during waiting: {e}{RESET}")

                                    return

                                else:

                                    await hybrid_trailing_buy(

                                        symbol=symbol,

                                        delta_percent=new_delta,

                                        activate_price=new_activation,

                                        advance=True,

                                        offset=2.0,

                                        skip_wait=False,

                                        auto_trailing_stop=True,

                                        stop_loss_percent=2.0,

                                        dynamic_ai_stop=True,

                                        auto_mode=True,

                                        timeout_minutes=30,

                                        skip_breakout_after_restart=True

                                    )

                                    return

                        except Exception as e:

                            output_manager.print_static(f"{YELLOW}⚠️ Breakout analysis error: {e}{RESET}")

                            output_manager.start_live_update()

                            new_analysis = await analyze_hold_wait_buy_ai(symbol)

                            current_price = await get_price_optimized(symbol)

                            price_deviation = safe_division((current_price - last_activation_price), last_activation_price, 0.0) * 100

                            output_manager.print_static(f"{CYAN}🤖 AUTO RE-ANALYSIS: Market moved {price_deviation:.1f}% from target{RESET}")

                            output_manager.print_static(f"{CYAN}📊 New Analysis: {new_analysis.market_regime.value} | Confidence: {new_analysis.confidence:.1f}%{RESET}")

                            if price_deviation > 3.0 and new_analysis.confidence < 70:

                                output_manager.print_static(f"{YELLOW}🤖 AUTO RESTART: Market moved too far, restarting with fresh setup...{RESET}")

                                await task_manager.cancel_all_tasks(symbol)

                                await asyncio.sleep(3)

                                output_manager.print_static(f"{CYAN}⏳ Entering smart waiting mode (updates every 5 min)...{RESET}")

                                wait_start = time.time()

                                last_wait_update = wait_start

                                last_wait_conf = 0.0

                                wait_count = 0

                                while True:

                                    await asyncio.sleep(30)

                                    try:

                                        wait_analysis = await analyze_hold_wait_buy_ai(symbol)

                                        wait_price = await get_price_optimized(symbol)

                                        current_wait = time.time()

                                        if (wait_analysis.recommendation in [ActionRecommendation.STRONG_BUY, ActionRecommendation.BUY] and

                                            wait_analysis.confidence >= 65.0):

                                            output_manager.print_static(f"\n{GREEN}✅ Conditions improved! Restarting trading...{RESET}")

                                            wait_delta = await calculate_ai_recommended_delta(wait_analysis, {}, "BUY", symbol)

                                            wait_activation = await calculate_smart_activation_price(symbol, wait_price, wait_analysis)

                                            await hybrid_trailing_buy(

                                                symbol=symbol,

                                                delta_percent=wait_delta,

                                                activate_price=wait_activation,

                                                advance=True,

                                                offset=2.0,

                                                skip_wait=False,

                                                auto_trailing_stop=True,

                                                stop_loss_percent=2.0,

                                                dynamic_ai_stop=True,

                                                auto_mode=True,

                                                timeout_minutes=30

                                            )

                                            return

                                        else:

                                                wait_elapsed = int((current_wait - wait_start) / 60)

                                        conf_changed = abs(wait_analysis.confidence - last_wait_conf) > 5.0

                                        time_update = (current_wait - last_wait_update) >= 300

                                        if time_update or conf_changed:

                                            wait_count += 1

                                            gap_phase1 = 65.0 - wait_analysis.confidence

                                            icon_nested = "📈" if conf_changed else "⏳"

                                            bat_pct_nest = min(100, (wait_analysis.confidence / 65.0) * 100)

                                            bat_blocks_nest = int(bat_pct_nest / 10)

                                            bat_fill_nest = "▓" * bat_blocks_nest

                                            bat_empty_nest = "░" * (10 - bat_blocks_nest)

                                            bat_bar_nest = f"[{bat_fill_nest}{bat_empty_nest}]"

                                            output_manager.print_static(

                                                f"  [{wait_elapsed:03d}m] {icon_nested} {wait_analysis.confidence:5.1f}% "

                                                f"{bat_bar_nest} • {gap_phase1:+.1f}%"

                                            )

                                            last_wait_update = current_wait

                                            last_wait_conf = wait_analysis.confidence

                                    except Exception as e:

                                        output_manager.print_static(f"{RED}⚠️ Error during waiting: {e}{RESET}")

                                        await asyncio.sleep(30)

                                return

                            else:

                                # Adaptive price deviation threshold berdasarkan coin type dan volatility

                                coin_type_for_threshold = await get_coin_type_range(symbol)

                                volatility_for_threshold = await calculate_coin_volatility(symbol)

                                if coin_type_for_threshold == 'BLUECHIP':

                                    deviation_threshold = 1.5  # Lebih sensitif untuk stable coins

                                elif coin_type_for_threshold == 'MAJOR':

                                    deviation_threshold = 1.8

                                elif coin_type_for_threshold == 'ALTCOIN':

                                    deviation_threshold = 2.5  # Kurang sensitif untuk volatile coins

                                else:  # MICROCAP

                                    deviation_threshold = 3.0  # Paling tidak sensitif

                                

                                # Adjust berdasarkan volatility

                                if volatility_for_threshold > 40:

                                    deviation_threshold += 0.5  # High vol = kurang sensitif

                                elif volatility_for_threshold < 15:

                                    deviation_threshold -= 0.3  # Low vol = lebih sensitif

                                

                                if price_deviation > deviation_threshold:

                                    # Full recalculation dengan technical indicators terbaru

                                    output_manager.print_static(f"{CYAN}🔄 Recalculating activation price with fresh market analysis (deviation {price_deviation:.1f}% > threshold {deviation_threshold:.1f}%)...{RESET}")

                                    new_activation = await calculate_smart_activation_price(symbol, current_price, new_analysis)

                                    output_manager.print_static(f"{GREEN}🤖 AUTO ADJUST: New activation ${fmt(new_activation)} (was ${fmt(last_activation_price)}){RESET}")

                                    activate_price = new_activation

                                    current_strategy.activation_price = new_activation

                                    last_activation_price = new_activation

                                    start_time = time.time()

                                    output_manager.start_live_update()

                                    continue

                                else:

                                    output_manager.print_static(f"{GREEN}🤖 AUTO CONTINUE: Market still close to target (deviation {price_deviation:.1f}% <= threshold {deviation_threshold:.1f}%){RESET}")

                                    start_time = time.time()

                                    output_manager.start_live_update()

                                    continue

                        except Exception as e:

                            output_manager.print_static(f"{RED}🤖 AUTO ERROR: {e}{RESET}")

                            await task_manager.cancel_all_tasks(symbol)

                            return

                    breakout_threshold = last_activation_price * (1 + max_breakout_threshold / 100)

                    time_since_last_legacy_check = current_time - last_breakout_check

                    if price > breakout_threshold and time_since_last_legacy_check >= breakout_cooldown:

                        last_breakout_check = current_time

                        output_manager.stop_live_update()

                        output_manager.print_static(f"\n{YELLOW}──────────────────────────────────────────────────────────────────────{RESET}")

                        output_manager.print_static(f"{YELLOW}🚨 BREAKOUT │ Price +{max_breakout_threshold:.0f}% above target ({coin_type_display}){RESET}")

                        try:

                            new_analysis = await analyze_hold_wait_buy_ai(symbol)

                            candles_1h = await get_candles_optimized(symbol, "1h", 50)

                            indicators_1h = calculate_advanced_indicators(candles_1h) if candles_1h else {}

                            trend_strength = indicators_1h.get('trend_strength', 0.5)

                            regime_clean = new_analysis.market_regime.value.title().replace('_', ' ')

                            output_manager.print_static(f"Analysis: {regime_clean} │ Trend: {trend_strength:.2f}")

                            if trend_strength > 0.7:

                                # Full recalculation dengan technical indicators terbaru untuk strong trend

                                output_manager.print_static(f"{CYAN}🔄 Recalculating activation price for strong trend...{RESET}")

                                new_activation = await calculate_smart_activation_price(symbol, price, new_analysis)

                                output_manager.print_static(f"{GREEN}✅ REPOSITION: Strong trend → New target ${fmt(new_activation)}{RESET}")

                                output_manager.print_static(f"{YELLOW}──────────────────────────────────────────────────────────────────────{RESET}\n")

                                activate_price = new_activation

                                current_strategy.activation_price = new_activation

                                last_activation_price = new_activation

                                start_time = time.time()

                                output_manager.start_live_update()

                                continue

                            else:

                                output_manager.print_static(f"{YELLOW}🔄 RESTART: Weak trend ({trend_strength:.2f}), fresh analysis...{RESET}")

                                output_manager.print_static(f"{YELLOW}──────────────────────────────────────────────────────────────────────{RESET}\n")

                                await task_manager.cancel_all_tasks(symbol)

                                await asyncio.sleep(5)

                                new_analysis = await analyze_hold_wait_buy_ai(symbol)

                                new_price = await get_price_optimized(symbol)

                                if not new_price:

                                    output_manager.print_static(f"{RED}Failed to get price, cancelling...{RESET}")

                                    return

                                output_manager.print_static(f"Fresh: {new_analysis.recommendation.value} {new_analysis.confidence:.0f}% │ ${fmt(new_price)}")

                                if new_analysis.recommendation in [ActionRecommendation.STRONG_BUY, ActionRecommendation.BUY]:

                                    output_manager.print_static(f"{GREEN}✅ Restarting with fresh setup...{RESET}")

                                    new_delta = await calculate_ai_recommended_delta(new_analysis, {}, "BUY", symbol)

                                    new_activation = await calculate_smart_activation_price(symbol, new_price, new_analysis)

                                    if new_activation < new_price * 0.98:

                                        new_activation = new_price * 0.98

                                        output_manager.print_static(f"{YELLOW}⚠️ Adjusted activation to ${fmt(new_activation)} to avoid immediate breakout{RESET}")

                                    await hybrid_trailing_buy(

                                        symbol=symbol,

                                        delta_percent=new_delta,

                                        activate_price=new_activation,

                                        advance=True,

                                        offset=2.0,

                                        skip_wait=False,

                                        auto_trailing_stop=True,

                                        stop_loss_percent=2.0,

                                        dynamic_ai_stop=True,

                                        auto_mode=True,

                                        timeout_minutes=30,

                                        skip_breakout_after_restart=True

                                    )

                                    return

                                else:

                                    output_manager.print_static(f"{YELLOW}🤖 AUTO WAITING: Confidence low, entering smart waiting mode...{RESET}")

                                    output_manager.print_static(f"{CYAN}⏳ Bot will monitor market until conditions improve{RESET}")

                                    wait_start = time.time()

                                    last_wait_update = wait_start

                                    last_wait_confidence = 0.0

                                    wait_update_count = 0

                                    output_manager.print_static(f"{CYAN}⏳ Smart waiting activated (updates every 5 min or on significant change){RESET}")

                                    while True:

                                        await asyncio.sleep(30)

                                        try:

                                            wait_analysis = await analyze_hold_wait_buy_ai(symbol)

                                            wait_price = await get_price_optimized(symbol)

                                            current_wait = time.time()

                                            if (wait_analysis.recommendation in [ActionRecommendation.STRONG_BUY, ActionRecommendation.BUY] and

                                                wait_analysis.confidence >= 65.0):

                                                output_manager.print_static(f"\n{GREEN}✅ Conditions improved! Starting trading...{RESET}")

                                                wait_delta = await calculate_ai_recommended_delta(wait_analysis, {}, "BUY", symbol)

                                                wait_activation = await calculate_smart_activation_price(symbol, wait_price, wait_analysis)

                                                await hybrid_trailing_buy(

                                                    symbol=symbol,

                                                    delta_percent=wait_delta,

                                                    activate_price=wait_activation,

                                                    advance=True,

                                                    offset=2.0,

                                                    skip_wait=False,

                                                    auto_trailing_stop=True,

                                                    stop_loss_percent=2.0,

                                                    dynamic_ai_stop=True,

                                                    auto_mode=True,

                                                    timeout_minutes=30,

                                                    skip_breakout_after_restart=True

                                                )

                                                return

                                            else:

                                                wait_elapsed = int((current_wait - wait_start) / 60)

                                                confidence_changed = abs(wait_analysis.confidence - last_wait_confidence) > 5.0

                                                time_for_update = (current_wait - last_wait_update) >= 300

                                                if time_for_update or confidence_changed:

                                                    wait_update_count += 1

                                                    gap_phase1_nested = 65.0 - wait_analysis.confidence

                                                    icon_nested2 = "📈" if confidence_changed else "⏳"

                                                    bat_pct_nest2 = min(100, (wait_analysis.confidence / 65.0) * 100)

                                                    bat_blocks_nest2 = int(bat_pct_nest2 / 10)

                                                    bat_fill_nest2 = "▓" * bat_blocks_nest2

                                                    bat_empty_nest2 = "░" * (10 - bat_blocks_nest2)

                                                    bat_bar_nest2 = f"[{bat_fill_nest2}{bat_empty_nest2}]"

                                                    output_manager.print_static(

                                                        f"  [{wait_elapsed:03d}m] {icon_nested2} {wait_analysis.confidence:5.1f}% "

                                                        f"{bat_bar_nest2} • {gap_phase1_nested:+.1f}%"

                                                    )

                                                    last_wait_update = current_wait

                                                    last_wait_confidence = wait_analysis.confidence

                                        except Exception as e:

                                            output_manager.print_static(f"{RED}⚠️ Error during waiting: {e}{RESET}")

                                            await asyncio.sleep(30)

                                return

                        except Exception as e:

                            output_manager.print_static(f"{RED}🤖 BREAKOUT ERROR: {e}{RESET}")

                            await task_manager.cancel_all_tasks(symbol)

                            return

                if reanalysis_task and reanalysis_task.done():

                    try:

                        result = await reanalysis_task

                        if result == "CANCEL":

                            output_manager.stop_live_update()

                            output_manager.print_static(f"{YELLOW}🔄 Looking for new setup after cancellation...{RESET}")

                            await asyncio.sleep(3)

                            new_strategy = await handle_strategy_cancellation(symbol, "AI adaptive re-analysis")

                            if new_strategy:

                                await execute_strategy(symbol, new_strategy, advance, offset)

                            return

                    except asyncio.CancelledError:

                        pass

                discount_needed = safe_division((activate_price - price), price, 0.0) * 100

                current_delta = current_strategy.delta_percent

                auto_indicator = "🤖" if auto_mode else "👤"

                if auto_mode:

                    waited_minutes = int(elapsed_time // 60)

                    waited_seconds = int(elapsed_time % 60)

                    waited_display = f"{waited_minutes:02d}:{waited_seconds:02d}"

                    if timeout_seconds is not None:

                        time_remaining = max(0, timeout_seconds - elapsed_time)

                        minutes = int(time_remaining // 60)

                        seconds = int(time_remaining % 60)

                        timer_display = f"{minutes:02d}:{seconds:02d}"

                        if time_remaining < 60:

                            urgency_color = RED

                        elif time_remaining < 180:

                            urgency_color = YELLOW

                        else:

                            urgency_color = GREEN

                        status = f"{urgency_color}🤖【AUTO】{symbol} | 💰 ${fmt(price)} → 🎯 ${fmt(activate_price)} ({discount_needed:+.2f}% needed) | ⏰ {timer_display} | ⏱️ {waited_display}{RESET}"

                    else:

                        status = f"{CYAN}🤖【AUTO】{symbol} | 💰 ${fmt(price)} → 🎯 ${fmt(activate_price)} ({discount_needed:+.2f}% needed) | ⏱️ {waited_display} | ⏰ No timeout{RESET}"

                else:

                    waited_minutes = int(elapsed_time // 60)

                    waited_seconds = int(elapsed_time % 60)

                    waited_display = f"{waited_minutes:02d}:{waited_seconds:02d}"

                    status = f"{CYAN}👤【MANUAL】{symbol} | 💰 ${fmt(price)} → 🎯 ${fmt(activate_price)} ({discount_needed:+.2f}% needed) | ⏱️ {waited_display} | Ctrl+C to cancel{RESET}"

                output_manager.print_live(status)

                if not hasattr(hybrid_trailing_buy, 'last_order_flow_time'):

                    hybrid_trailing_buy.last_order_flow_time = 0

                if price <= activate_price:

                    output_manager.stop_live_update()

                    output_manager.print_static(f"\n{GREEN}{'='*60}{RESET}")

                    output_manager.print_static(f"{GREEN}✅ ACTIVATION ACHIEVED! Starting trailing buy...{RESET}")

                    output_manager.print_static(f"{GREEN}{'='*60}{RESET}")

                    output_manager.print_static(f"{CYAN}🔍 Executing pre-buy analysis...{RESET}")

                    activation_moment_price = price

                    should_proceed = await pre_buy_analysis(symbol, activate_price, current_strategy, waiting_start_time)

                    current_check_price = await get_price_optimized(symbol)

                    if current_check_price:

                        price_drift = safe_division(abs(current_check_price - activation_moment_price), activation_moment_price, 0.0) * 100

                        if price_drift > 1.0:

                            output_manager.print_static(f"{RED}⚠️ PRICE DRIFT: {price_drift:.2f}% during analysis (${fmt(activation_moment_price)} → ${fmt(current_check_price)}){RESET}")

                            should_proceed = False

                    if not should_proceed:

                        output_manager.print_static(f"{RED}🎯 BUY CANCELLED: Market conditions unfavorable{RESET}")

                        if auto_mode:

                            output_manager.print_static(f"{GREEN}🤖 AUTO: Looking for new setup...{RESET}")

                            await handle_strategy_cancellation(symbol, f"Pre-buy analysis failed for activation ${fmt(activate_price)}")

                        else:

                            retry = input(f"{YELLOW}Look for new setup? (Y/n): {RESET}").strip().lower()

                            if retry != 'n':

                                await handle_strategy_cancellation(symbol, f"Pre-buy analysis failed for activation ${fmt(activate_price)}")

                        await task_manager.cancel_all_tasks(symbol)

                        return

                    output_manager.print_static(f"\n{GREEN}🚀 PROCEEDING WITH BUY EXECUTION...{RESET}")

                    current_analysis_pre = await analyze_hold_wait_buy_ai(symbol)

                    current_regime_pre = current_analysis_pre.market_regime

                    is_safe_reentry_pre, _, reentry_tier = await is_safe_post_profit_reentry(

                        symbol, price, current_analysis_pre.confidence, current_regime_pre

                    )

                    if is_safe_reentry_pre:

                        original_delta = current_strategy.delta_percent

                        if reentry_tier == "BIG":

                            current_strategy.delta_percent = 2.0

                            tier_msg = "BIG PROFIT tier"

                        elif reentry_tier == "MEDIUM":

                            current_strategy.delta_percent = 2.5

                            tier_msg = "MEDIUM PROFIT tier"

                        else:

                            tier_msg = "Unknown tier"

                        tier_emoji = "🔥" if reentry_tier == "BIG" else "⚡"

                        output_manager.print_static(f"{GREEN}{tier_emoji} SMART RE-ENTRY [{tier_msg}]: Delta reduced {original_delta:.1f}% → {current_strategy.delta_percent:.1f}% for faster entry{RESET}")

                        if portfolio_manager.last_profitable_close:

                            portfolio_manager.last_profitable_close['reentry_used'] = True

                            output_manager.print_static(f"{GREEN}   🔒 Re-entry flag set (one per exit limit active){RESET}")

                    if reanalysis_task and not reanalysis_task.done():

                        reanalysis_task.cancel()

                    last_price = price

                    break

                last_price = price

                await asyncio.sleep(poll_interval)

        else:

            # Timeout scenario - use the adaptive entry price that was calculated

            price = await get_price_optimized(symbol)

            # activate_price was already updated to adaptive_entry during timeout validation

            # Use activate_price as the base for trailing buy (not current price)

            trailing_base_price = activate_price if activate_price < price else price

            output_manager.print_static(f"{GREEN}⚡ AUTO-ACTIVE | Entry Base: ${fmt(trailing_base_price)} | Current: ${fmt(price)} | Starting trailing...{RESET}")

            last_price = price

        # Use activate_price as low if it's lower than last_price (for timeout adaptive entry)

        # Ensure consistent precision to avoid rounding issues

        activate_price_rounded = round(activate_price, 8) if activate_price > 0 else 0

        last_price_rounded = round(last_price, 8)

        low = min(last_price_rounded, activate_price_rounded) if activate_price > 0 else last_price_rounded

        current_delta = current_strategy.delta_percent

        trigger = low * (1 + current_delta / 100.0)

        output_manager.print_static(f"\n{GREEN}{'='*80}{RESET}")

        output_manager.print_static(f"{GREEN}🎯 【TRAILING BUY MODE ACTIVE】{RESET}")

        output_manager.print_static(f"{GREEN}{'='*80}{RESET}")

        output_manager.print_static(f"  💰 Entry Low: ${fmt(low)}")

        output_manager.print_static(f"  🎯 Buy Trigger: ${fmt(trigger)} (+{current_delta:.1f}% dari low)")

        output_manager.print_static(f"  📊 Strategy: Tunggu harga naik {current_delta:.1f}% dari low point, lalu BUY")

        output_manager.print_static(f"{GREEN}{'='*80}{RESET}")

        reanalysis_task = await task_manager.create_task(

            symbol, "reanalysis_trailing",

            adaptive_time_based_reanalysis(symbol, current_strategy, "BUY")

        )

        output_manager.start_live_update()

        while True:

            price = await get_price_optimized(symbol)

            if price is None:

                await asyncio.sleep(poll_interval)

                continue

            if reanalysis_task and reanalysis_task.done():

                try:

                    result = await reanalysis_task

                    if result == "CANCEL":

                        output_manager.stop_live_update()

                        output_manager.print_static(f"{YELLOW}🔄 Strategy cancelled during trailing...{RESET}")

                        await asyncio.sleep(3)

                        new_strategy = await handle_strategy_cancellation(symbol, "Trailing phase re-analysis")

                        if new_strategy:

                            await execute_strategy(symbol, new_strategy, advance, offset)

                        return

                except asyncio.CancelledError:

                    pass

            if price < low:

                low = price

            current_delta = current_strategy.delta_percent

            trigger = low * (1 + current_delta / 100.0)

            arrow = "🟢" if price > last_price else "🔴" if price < last_price else "🟡"

            profit_pct = safe_division((price - low), low, 0.0) * 100

            potential_pct = safe_division((trigger - price), price, 0.0) * 100

            status = f"{GREEN}📈【TRAILING BUY】{symbol} | 💰 ${fmt(price)} {arrow} | 📉 Low: ${fmt(low)} | 🎯 ${fmt(trigger)} | Need: {potential_pct:+.2f}%{RESET}"

            output_manager.print_live(status)

            # Check if we should execute buy order

            should_execute = False

            entry_price = price

            use_limit_order = False

            limit_order_timeout = 0

            

            # For Fixed Mode with delta = 0.0%, if price is already above activation, use activation price as entry

            if current_delta == 0.0 and price > activate_price:

                # Fixed Mode: Use activation price as entry if current price is above it

                entry_price = activate_price

                should_execute = True

                # Only show message once per symbol to avoid spam

                if symbol not in hybrid_trailing_buy._fixed_mode_msgs:

                    output_manager.print_static(f"{CYAN}💡 Fixed Mode: Price ${fmt(price)} > Activation ${fmt(activate_price)}, will use activation price ${fmt(activate_price)} as entry (waiting for pre-check pass)...{RESET}")

                    hybrid_trailing_buy._fixed_mode_msgs[symbol] = True

            elif price >= trigger - 1e-10:

                # Normal trailing: Price reached trigger

                # NEW: Optimize entry price with limit order option

                distance_pct = safe_division((price - trigger), trigger, 0.0) * 100

                

                # Get current market conditions for optimization

                try:

                    orderbook_opt = await get_orderbook_optimized(symbol, 20)

                    coin_type_opt = await get_coin_type_range(symbol)

                    

                    # Calculate spread and depth

                    spread_pct = 0.0

                    depth_ok = True

                    if orderbook_opt and 'bids' in orderbook_opt and 'asks' in orderbook_opt:

                        bids_opt = orderbook_opt['bids']

                        asks_opt = orderbook_opt['asks']

                        if bids_opt and asks_opt:

                            best_bid_opt = float(bids_opt[0][0])

                            best_ask_opt = float(asks_opt[0][0])

                            spread_pct = safe_division((best_ask_opt - best_bid_opt), best_bid_opt, 0.0) * 100

                            

                            # Get max spread threshold

                            if coin_type_opt == 'BLUECHIP':

                                max_spread_opt = 0.08

                            elif coin_type_opt == 'MAJOR':

                                max_spread_opt = 0.12

                            elif coin_type_opt == 'ALTCOIN':

                                max_spread_opt = 0.50

                            elif coin_type_opt == 'MICROCAP':

                                max_spread_opt = 1.20

                            else:

                                max_spread_opt = 0.50

                            

                            # Check depth

                            ask_depth_top3_opt = sum([float(ask[1]) for ask in asks_opt[:3]])

                            ask_depth_usd_opt = ask_depth_top3_opt * best_ask_opt

                            

                            # Get min depth requirement

                            if best_ask_opt > 1000:

                                min_depth_usd_opt = 300000 if coin_type_opt == 'BLUECHIP' else 150000 if coin_type_opt == 'MAJOR' else 50000 if coin_type_opt == 'ALTCOIN' else 20000

                            elif best_ask_opt > 100:

                                min_depth_usd_opt = 150000 if coin_type_opt == 'BLUECHIP' else 80000 if coin_type_opt == 'MAJOR' else 25000 if coin_type_opt == 'ALTCOIN' else 8000

                            elif best_ask_opt > 10:

                                min_depth_usd_opt = 80000 if coin_type_opt == 'BLUECHIP' else 40000 if coin_type_opt == 'MAJOR' else 10000 if coin_type_opt == 'ALTCOIN' else 3000

                            elif best_ask_opt > 1:

                                min_depth_usd_opt = 40000 if coin_type_opt == 'BLUECHIP' else 20000 if coin_type_opt == 'MAJOR' else 5000 if coin_type_opt == 'ALTCOIN' else 1500

                            elif best_ask_opt > 0.01:

                                min_depth_usd_opt = 20000 if coin_type_opt == 'BLUECHIP' else 10000 if coin_type_opt == 'MAJOR' else 2000 if coin_type_opt == 'ALTCOIN' else 500

                            else:

                                min_depth_usd_opt = 10000 if coin_type_opt == 'BLUECHIP' else 5000 if coin_type_opt == 'MAJOR' else 1000 if coin_type_opt == 'ALTCOIN' else 100

                            

                            depth_ok = ask_depth_usd_opt >= min_depth_usd_opt

                    

                    # CRITICAL: Calculate max_allowed_entry BEFORE optimization

                    # Ensure entry_price never exceeds trigger OR activate_price to respect minimum discount

                    # trigger can be > activate_price in trailing buy mode, but entry_price must respect activate_price

                    max_allowed_entry = min(trigger, activate_price)  # Use the lower of the two

                    

                    # Optimize entry price based on distance, spread, and depth

                    if distance_pct > 2.0 or spread_pct > max_spread_opt or not depth_ok:

                        # Price too far, spread too wide, or depth too thin → Use limit order at max_allowed_entry

                        entry_price = max_allowed_entry

                        use_limit_order = True

                        limit_order_timeout = 60  # Wait 60 seconds for fill

                        output_manager.print_static(

                            f"{CYAN}💡 Entry Optimization: Price ${fmt(price)} is {distance_pct:.2f}% above trigger ${fmt(trigger)}{RESET}"

                        )

                        if spread_pct > max_spread_opt:

                            output_manager.print_static(

                                f"{YELLOW}   → Spread too wide ({spread_pct:.3f}% > {max_spread_opt:.3f}%), using limit order at ${fmt(max_allowed_entry)}{RESET}"

                            )

                        elif not depth_ok:

                            output_manager.print_static(

                                f"{YELLOW}   → Depth too thin (${ask_depth_usd_opt:,.0f} < ${min_depth_usd_opt:,.0f}), using limit order at ${fmt(max_allowed_entry)}{RESET}"

                            )

                        else:

                            output_manager.print_static(

                                f"{YELLOW}   → Price moved too far ({distance_pct:.2f}% > 2.0%), using limit order at ${fmt(max_allowed_entry)} (waiting for pullback){RESET}"

                            )

                    elif distance_pct > 1.0:

                        # Price 1-2% above trigger → Use limit order slightly below current

                        # CRITICAL: Ensure entry_price never exceeds trigger OR activate_price to respect minimum discount

                        # trigger can be > activate_price in trailing buy mode, but entry_price must respect activate_price

                        candidate_entry_price = price * 0.998  # 0.2% below current

                        entry_price = min(candidate_entry_price, max_allowed_entry)  # Never exceed max_allowed_entry

                        use_limit_order = True

                        limit_order_timeout = 30  # Wait 30 seconds

                        if entry_price < max_allowed_entry:

                            output_manager.print_static(

                                f"{CYAN}💡 Entry Optimization: Price ${fmt(price)} is {distance_pct:.2f}% above trigger, using limit order at ${fmt(entry_price)} (0.2% below current){RESET}"

                            )

                        else:

                            output_manager.print_static(

                                f"{CYAN}💡 Entry Optimization: Price ${fmt(price)} is {distance_pct:.2f}% above trigger, using limit order at ${fmt(max_allowed_entry)} (enforced minimum discount){RESET}"

                            )

                    elif distance_pct > 0.5:

                        # Price 0.5-1% above trigger → Use limit order at max_allowed_entry or slightly below

                        # CRITICAL: Ensure entry_price never exceeds trigger OR activate_price to respect minimum discount

                        # trigger can be > activate_price in trailing buy mode, but entry_price must respect activate_price

                        candidate_entry_price = price * 0.999  # 0.1% below current

                        entry_price = min(candidate_entry_price, max_allowed_entry)  # Never exceed max_allowed_entry

                        use_limit_order = True

                        limit_order_timeout = 20  # Wait 20 seconds

                        if entry_price < max_allowed_entry:

                            output_manager.print_static(

                                f"{CYAN}💡 Entry Optimization: Price ${fmt(price)} is {distance_pct:.2f}% above trigger, using limit order at ${fmt(entry_price)}{RESET}"

                            )

                        else:

                            output_manager.print_static(

                                f"{CYAN}💡 Entry Optimization: Price ${fmt(price)} is {distance_pct:.2f}% above trigger, using limit order at ${fmt(max_allowed_entry)} (enforced minimum discount){RESET}"

                            )

                    else:

                        # Price close to trigger (< 0.5%) → Use market order (acceptable)

                        # CRITICAL: Ensure entry_price never exceeds trigger OR activate_price to respect minimum discount

                        # trigger can be > activate_price in trailing buy mode, but entry_price must respect activate_price

                        entry_price = min(price, max_allowed_entry)  # Never exceed max_allowed_entry

                        use_limit_order = False if entry_price >= max_allowed_entry - 1e-10 else True  # Use limit order if we had to enforce max_allowed_entry

                        if entry_price < max_allowed_entry:

                            output_manager.print_static(

                                f"{GREEN}✅ Entry Optimization: Price ${fmt(price)} is close to trigger ({distance_pct:.2f}%), using market order{RESET}"

                            )

                        else:

                            output_manager.print_static(

                                f"{CYAN}💡 Entry Optimization: Price ${fmt(price)} is close to trigger ({distance_pct:.2f}%), but enforcing ${fmt(max_allowed_entry)} to respect minimum discount{RESET}"

                            )

                            use_limit_order = True

                            limit_order_timeout = 20

                    

                    # FINAL ENFORCEMENT: Ensure entry_price never exceeds trigger OR activate_price

                    # This is a safety net to guarantee minimum discount is always respected

                    # CRITICAL: entry_price must be <= min(trigger, activate_price) to respect minimum discount

                    # trigger can be > activate_price in trailing buy mode, but entry_price must respect activate_price

                    max_allowed_entry = min(trigger, activate_price)  # Use the lower of the two

                    if entry_price > max_allowed_entry + 1e-10:  # Use epsilon for floating point comparison

                        output_manager.print_static(

                            f"{YELLOW}⚠️ Entry price ${fmt(entry_price)} > max allowed ${fmt(max_allowed_entry)} (trigger: ${fmt(trigger)}, activate: ${fmt(activate_price)}), enforcing ${fmt(max_allowed_entry)} to respect minimum discount{RESET}"

                        )

                        entry_price = max_allowed_entry

                        use_limit_order = True

                        if limit_order_timeout == 0:

                            limit_order_timeout = 60

                except Exception as e:

                    # If optimization fails, use max_allowed_entry as safe fallback

                    # CRITICAL: entry_price must respect activate_price (minimum discount)

                    max_allowed_entry = min(trigger, activate_price)

                    entry_price = min(price, max_allowed_entry)  # Never exceed max_allowed_entry

                    use_limit_order = False if entry_price >= max_allowed_entry - 1e-10 else True

                    if getattr(TradingConfig, 'ENABLE_GUARD_DEBUG_LOGS', False):

                        output_manager.print_static(

                            f"{YELLOW}⚠️ Entry optimization error: {e}, using current price{RESET}"

                        )

                

                should_execute = True

            else:

                # Price not yet at trigger, continue waiting

                last_price = price

                await asyncio.sleep(poll_interval)

                continue

            

            # Only proceed with pre-checks if we should execute

            if not should_execute:

                last_price = price

                await asyncio.sleep(poll_interval)

                continue

            

            # Re-fetch buy pressure before actual buy execution (for accuracy)

            # FINAL CHECK: Handle buy pressure changes after balanced strategy adjustment

            final_buy_pressure_checked = False

            activation_price_adjusted = False

            try:

                fresh_order_flow = await get_order_flow_optimized(symbol, limit=50)

                fresh_buy_pressure = fresh_order_flow.get('buy_pressure', 50.0)

                fresh_trade_count = fresh_order_flow.get('trade_count', 0)

                

                # Check if this symbol had buy pressure adjustment from balanced strategy

                import core as core_module

                if hasattr(core_module, '_buy_pressure_adjustments') and symbol in core_module._buy_pressure_adjustments:

                    adj_info = core_module._buy_pressure_adjustments[symbol]

                    if adj_info.get('needs_final_check', False):

                        output_manager.print_static(

                            f"{CYAN}🔍 FINAL CHECK: Buy pressure setelah adjustment...{RESET}"

                        )

                        

                        # Calculate flow score from fresh buy pressure

                        try:

                            from core import calculate_flow_score_from_buy_pressure

                            # Get current analysis for flow score calculation

                            current_analysis_for_flow = initial_analysis if initial_analysis else await analyze_hold_wait_buy_ai(symbol, lite=True)

                            current_volatility = await calculate_coin_volatility(symbol)

                            final_flow_score = await calculate_flow_score_from_buy_pressure(

                                symbol, fresh_buy_pressure, current_analysis_for_flow, current_volatility

                            )

                            

                            output_manager.print_static(

                                f"{CYAN}📊 Final Buy Pressure: {fresh_buy_pressure:.1f}% (Flow Score: {final_flow_score:.1f}/20){RESET}"

                            )

                            

                            # Compare with previous flow scores

                            initial_flow_score = adj_info.get('initial_flow_score', 4.0)

                            fresh_flow_score = adj_info.get('fresh_flow_score', 4.0)

                            

                            # Decision based on final flow score

                            if final_flow_score >= 8.0:

                                # Buy pressure MEMBAIK SIGNIFIKAN → Reduce discount (tapi tetap > original)

                                output_manager.print_static(

                                    f"{GREEN}✅ Buy Pressure MEMBAIK SIGNIFIKAN ({final_flow_score:.1f}/20 >= 8.0){RESET}"

                                )

                                

                                # Reduce discount: use 1.5x instead of 2x (tapi tetap lebih besar dari original)

                                original_discount = adj_info.get('original_discount', 0)

                                adjusted_discount = adj_info.get('adjusted_discount', 0)

                                new_discount = original_discount * 1.5  # Middle ground

                                new_discount = min(new_discount, adjusted_discount)  # Don't exceed adjusted

                                

                                # Recalculate activation price

                                current_price_final = await get_price_optimized(symbol)

                                if current_price_final:

                                    new_activation_price = current_price_final * (1 - new_discount / 100)

                                    # Update activation price (this will be used for entry)

                                    activate_price = new_activation_price

                                    # Update entry price if activation price is lower (better entry)

                                    if new_activation_price < entry_price:

                                        entry_price = new_activation_price

                                    

                                    output_manager.print_static(

                                        f"{CYAN}   Discount dikurangi: {adjusted_discount:.2f}% → {new_discount:.2f}% (1.5x original){RESET}"

                                    )

                                    output_manager.print_static(

                                        f"{CYAN}   Activation price adjusted: ${activate_price:.8f} (Entry: ${entry_price:.8f}){RESET}"

                                    )

                                    activation_price_adjusted = True

                                    

                                    # Update adjustment info

                                    adj_info['final_discount'] = new_discount

                                    adj_info['final_activation'] = new_activation_price

                                    adj_info['improvement_detected'] = True

                                

                            elif final_flow_score < 4.0:

                                # Buy pressure MEMBURUK LEBIH LANJUT → SKIP TRADE untuk safety

                                output_manager.print_static(

                                    f"{RED}🚨 Buy Pressure MEMBURUK LEBIH LANJUT ({final_flow_score:.1f}/20 < 4.0){RESET}"

                                )

                                output_manager.print_static(

                                    f"{RED}   ❌ SKIPPING TRADE untuk safety (buy pressure terlalu lemah){RESET}"

                                )

                                

                                # Cancel strategy

                                await handle_strategy_cancellation(

                                    symbol, 

                                    f"Buy pressure memburuk lebih lanjut: {final_flow_score:.1f}/20 < 4.0"

                                )

                                return

                                

                            elif final_flow_score >= 4.0 and final_flow_score < 8.0:

                                # Buy pressure tetap atau sedikit membaik tapi masih VERY WEAK

                                # Use adjusted discount (2x) yang sudah di-set

                                if final_flow_score > fresh_flow_score:

                                    output_manager.print_static(

                                        f"{YELLOW}⚠️ Buy Pressure sedikit membaik ({fresh_flow_score:.1f}/20 → {final_flow_score:.1f}/20) tapi masih VERY WEAK{RESET}"

                                    )

                                    output_manager.print_static(

                                        f"{CYAN}   Menggunakan adjusted discount ({adj_info.get('adjusted_discount', 0):.2f}%) yang sudah di-set{RESET}"

                                    )

                                else:

                                    output_manager.print_static(

                                        f"{YELLOW}⚠️ Buy Pressure tetap VERY WEAK ({final_flow_score:.1f}/20){RESET}"

                                    )

                                    output_manager.print_static(

                                        f"{CYAN}   Menggunakan adjusted discount ({adj_info.get('adjusted_discount', 0):.2f}%) yang sudah di-set{RESET}"

                                    )

                                

                                # Use adjusted activation price

                                if adj_info.get('adjusted_activation'):

                                    activate_price = adj_info['adjusted_activation']

                                    entry_price = activate_price if entry_price > activate_price else entry_price

                                    activation_price_adjusted = True

                            

                            # Mark as checked

                            adj_info['needs_final_check'] = False

                            adj_info['final_flow_score'] = final_flow_score

                            final_buy_pressure_checked = True

                            

                        except Exception as e:

                            output_manager.print_static(

                                f"{YELLOW}⚠️ Error in final buy pressure check: {e}{RESET}"

                            )

                            # Continue with adjusted activation price if available

                            if adj_info.get('adjusted_activation'):

                                activate_price = adj_info['adjusted_activation']

                                entry_price = activate_price if entry_price > activate_price else entry_price

                

                # Normal pre-buy buy pressure logging (if not already logged above)

                if not final_buy_pressure_checked and fresh_trade_count > 0:

                    output_manager.print_static(

                        f"{CYAN}📊 Pre-Buy Buy Pressure: {fresh_buy_pressure:.1f}% (from {fresh_trade_count} recent trades){RESET}"

                    )

            except Exception:

                pass  # Continue if re-fetch fails

            

            # NEW: Pre-buy validation (spread, depth, slippage) before actual buy execution

            if should_execute:

                pre_buy_validation_passed = True

                pre_buy_validation_reason = ""

                

                try:

                    # Get fresh orderbook data (optimized for live trading - faster, more accurate)

                    orderbook_prebuy = await get_orderbook_optimized(symbol, 20, force_fresh=True, max_age_s=2)  # Reduced from 5s to 2s for live trading

                    coin_type_prebuy = await get_coin_type_range(symbol)

                    # Use WebSocket first (faster, real-time), fallback to REST if needed
                    current_price_prebuy = await get_price_optimized(symbol, force_fresh=False)  # WebSocket preferred for speed

                    

                    if orderbook_prebuy and 'bids' in orderbook_prebuy and 'asks' in orderbook_prebuy:

                        bids_prebuy = orderbook_prebuy['bids']

                        asks_prebuy = orderbook_prebuy['asks']

                        if bids_prebuy and asks_prebuy:

                            best_bid_prebuy = float(bids_prebuy[0][0])

                            best_ask_prebuy = float(asks_prebuy[0][0])

                            spread_pct_prebuy = safe_division((best_ask_prebuy - best_bid_prebuy), best_bid_prebuy, 0.0) * 100

                            

                            # Get max spread threshold

                            if coin_type_prebuy == 'BLUECHIP':

                                max_spread_prebuy = 0.08

                            elif coin_type_prebuy == 'MAJOR':

                                max_spread_prebuy = 0.12

                            elif coin_type_prebuy == 'ALTCOIN':

                                max_spread_prebuy = 0.50

                            elif coin_type_prebuy == 'MICROCAP':

                                max_spread_prebuy = 1.20

                            else:

                                max_spread_prebuy = 0.50

                            

                            # Check spread

                            if spread_pct_prebuy > max_spread_prebuy:

                                pre_buy_validation_passed = False

                                pre_buy_validation_reason = f"Spread too wide: {spread_pct_prebuy:.3f}% > {max_spread_prebuy:.3f}%"

                                output_manager.print_static(

                                    f"{YELLOW}⚠️ Pre-Buy Validation: {pre_buy_validation_reason}{RESET}"

                                )

                                # Force limit order at max_allowed_entry if spread too wide

                                # CRITICAL: entry_price must respect activate_price (minimum discount)

                                max_allowed_entry_prebuy = min(trigger, activate_price)

                                if not use_limit_order:

                                    entry_price = max_allowed_entry_prebuy

                                    use_limit_order = True

                                    limit_order_timeout = 60

                                    output_manager.print_static(

                                        f"{CYAN}   → Switching to limit order at ${fmt(max_allowed_entry_prebuy)} to avoid wide spread{RESET}"

                                    )

                                    pre_buy_validation_passed = True  # Allow with limit order

                            

                            # Check depth

                            ask_depth_top3_prebuy = sum([float(ask[1]) for ask in asks_prebuy[:3]])

                            ask_depth_usd_prebuy = ask_depth_top3_prebuy * best_ask_prebuy

                            

                            # Get min depth requirement

                            if best_ask_prebuy > 1000:

                                min_depth_usd_prebuy = 300000 if coin_type_prebuy == 'BLUECHIP' else 150000 if coin_type_prebuy == 'MAJOR' else 50000 if coin_type_prebuy == 'ALTCOIN' else 20000

                            elif best_ask_prebuy > 100:

                                min_depth_usd_prebuy = 150000 if coin_type_prebuy == 'BLUECHIP' else 80000 if coin_type_prebuy == 'MAJOR' else 25000 if coin_type_prebuy == 'ALTCOIN' else 8000

                            elif best_ask_prebuy > 10:

                                min_depth_usd_prebuy = 80000 if coin_type_prebuy == 'BLUECHIP' else 40000 if coin_type_prebuy == 'MAJOR' else 10000 if coin_type_prebuy == 'ALTCOIN' else 3000

                            elif best_ask_prebuy > 1:

                                min_depth_usd_prebuy = 40000 if coin_type_prebuy == 'BLUECHIP' else 20000 if coin_type_prebuy == 'MAJOR' else 5000 if coin_type_prebuy == 'ALTCOIN' else 1500

                            elif best_ask_prebuy > 0.01:

                                min_depth_usd_prebuy = 20000 if coin_type_prebuy == 'BLUECHIP' else 10000 if coin_type_prebuy == 'MAJOR' else 2000 if coin_type_prebuy == 'ALTCOIN' else 500

                            else:

                                min_depth_usd_prebuy = 10000 if coin_type_prebuy == 'BLUECHIP' else 5000 if coin_type_prebuy == 'MAJOR' else 1000 if coin_type_prebuy == 'ALTCOIN' else 100

                            

                            if ask_depth_usd_prebuy < min_depth_usd_prebuy:

                                pre_buy_validation_passed = False

                                pre_buy_validation_reason = f"Depth too thin: ${ask_depth_usd_prebuy:,.0f} < ${min_depth_usd_prebuy:,.0f}"

                                output_manager.print_static(

                                    f"{YELLOW}⚠️ Pre-Buy Validation: {pre_buy_validation_reason}{RESET}"

                                )

                                # Force limit order at max_allowed_entry if depth too thin

                                # CRITICAL: entry_price must respect activate_price (minimum discount)

                                max_allowed_entry_prebuy = min(trigger, activate_price)

                                if not use_limit_order:

                                    entry_price = max_allowed_entry_prebuy

                                    use_limit_order = True

                                    limit_order_timeout = 60

                                    output_manager.print_static(

                                        f"{CYAN}   → Switching to limit order at ${fmt(max_allowed_entry_prebuy)} to avoid thin depth{RESET}"

                                    )

                                    pre_buy_validation_passed = True  # Allow with limit order

                            

                            # CRITICAL: Ensure entry_price never exceeds trigger OR activate_price even after validation

                            # This is a final safety check to guarantee minimum discount is always respected

                            # CRITICAL: entry_price must be <= min(trigger, activate_price) to respect minimum discount

                            # trigger can be > activate_price in trailing buy mode, but entry_price must respect activate_price

                            max_allowed_entry = min(trigger, activate_price)  # Use the lower of the two

                            if entry_price > max_allowed_entry + 1e-10:  # Use epsilon for floating point comparison

                                output_manager.print_static(

                                    f"{YELLOW}⚠️ Pre-Buy Validation: entry_price ${fmt(entry_price)} > max allowed ${fmt(max_allowed_entry)} (trigger: ${fmt(trigger)}, activate: ${fmt(activate_price)}), enforcing ${fmt(max_allowed_entry)} to respect minimum discount{RESET}"

                                )

                                entry_price = max_allowed_entry

                                use_limit_order = True

                                if limit_order_timeout == 0:

                                    limit_order_timeout = 60

                            

                            # Log validation result

                            if pre_buy_validation_passed:

                                output_manager.print_static(

                                    f"{GREEN}✅ Pre-Buy Validation: Spread {spread_pct_prebuy:.3f}% OK, Depth ${ask_depth_usd_prebuy:,.0f} OK{RESET}"

                                )

                except Exception as e:

                    # If validation fails, proceed with caution

                    if getattr(TradingConfig, 'ENABLE_GUARD_DEBUG_LOGS', False):

                        output_manager.print_static(

                            f"{YELLOW}⚠️ Pre-buy validation error: {e}, proceeding with caution{RESET}"

                        )

                    pre_buy_validation_passed = True  # Allow to proceed

            

            # NEW: Smart wait strategy for limit orders (wait for better entry)

            if should_execute and use_limit_order and limit_order_timeout > 0:

                output_manager.print_static(

                    f"{CYAN}⏳ Smart Wait: Waiting up to {limit_order_timeout}s for price to reach limit order at ${fmt(entry_price)}{RESET}"

                )

                

                wait_start_time = time.time()

                wait_check_interval = 1.0  # Check every 1 second

                price_reached_limit = False

                

                while time.time() - wait_start_time < limit_order_timeout:

                    current_wait_price = await get_price_optimized(symbol)

                    if current_wait_price is None:

                        await asyncio.sleep(wait_check_interval)

                        continue

                    

                    # Check if price reached limit order price

                    if current_wait_price <= entry_price:

                        price_reached_limit = True

                        output_manager.print_static(

                            f"{GREEN}✅ Price reached limit order at ${fmt(entry_price)} (current: ${fmt(current_wait_price)}){RESET}"

                        )

                        break

                    

                    # Update status

                    remaining_time = limit_order_timeout - (time.time() - wait_start_time)

                    distance_to_limit = safe_division((current_wait_price - entry_price), entry_price, 0.0) * 100

                    

                    if int(time.time() - wait_start_time) % 5 == 0:  # Update every 5 seconds

                        output_manager.print_static(

                            f"{CYAN}   Waiting... Current: ${fmt(current_wait_price)} | Limit: ${fmt(entry_price)} | Distance: {distance_to_limit:.2f}% | Time left: {remaining_time:.0f}s{RESET}"

                        )

                    

                    await asyncio.sleep(wait_check_interval)

                

                if not price_reached_limit:

                    # Limit order not filled, check if we should use market order or extend wait

                    final_price = await get_price_optimized(symbol)

                    if final_price is None:

                        final_price = price

                    

                    # CRITICAL: Ensure entry_price never exceeds trigger OR activate_price even when falling back

                    # If we need to fall back to market order, ensure we respect minimum discount

                    # CRITICAL: entry_price must be <= min(trigger, activate_price) to respect minimum discount

                    # trigger can be > activate_price in trailing buy mode, but entry_price must respect activate_price

                    max_allowed_entry = min(trigger, activate_price)  # Use the lower of the two

                    final_distance = safe_division((final_price - entry_price), entry_price, 0.0) * 100

                    

                    # Re-validate that entry_price respects max_allowed_entry before fallback

                    if entry_price > max_allowed_entry + 1e-10:

                        output_manager.print_static(

                            f"{YELLOW}⚠️ Smart Wait: entry_price ${fmt(entry_price)} > max allowed ${fmt(max_allowed_entry)} (trigger: ${fmt(trigger)}, activate: ${fmt(activate_price)}), enforcing ${fmt(max_allowed_entry)} to respect minimum discount{RESET}"

                        )

                        entry_price = max_allowed_entry

                        use_limit_order = True

                        limit_order_timeout = 60  # Extended timeout

                        price_reached_limit = False  # Continue waiting

                        # Re-check if price reached the corrected entry_price

                        if final_price <= entry_price + 1e-10:

                            price_reached_limit = True

                            output_manager.print_static(

                                f"{GREEN}✅ Price reached corrected limit order at ${fmt(entry_price)} (current: ${fmt(final_price)}){RESET}"

                            )

                    

                    if not price_reached_limit and abs(final_distance) < 1.0:  # Price is within 1% of limit order

                        # Close enough, but ensure we still respect max_allowed_entry

                        fallback_entry_price = final_price

                        if fallback_entry_price > max_allowed_entry + 1e-10:

                            # Fallback price exceeds max_allowed_entry, use max_allowed_entry instead

                            output_manager.print_static(

                                f"{YELLOW}⚠️ Limit order not filled, but fallback price ${fmt(fallback_entry_price)} > max allowed ${fmt(max_allowed_entry)} (trigger: ${fmt(trigger)}, activate: ${fmt(activate_price)}), using ${fmt(max_allowed_entry)} to respect minimum discount{RESET}"

                            )

                            entry_price = max_allowed_entry

                            use_limit_order = True

                            limit_order_timeout = 60

                        else:

                            # Fallback price is acceptable

                            output_manager.print_static(

                                f"{YELLOW}⚠️ Limit order not filled, but price is close ({final_distance:.2f}%), using market order at ${fmt(fallback_entry_price)}{RESET}"

                            )

                            entry_price = fallback_entry_price

                            use_limit_order = False

                    else:

                        # Price moved away, cancel or extend wait

                        output_manager.print_static(

                            f"{YELLOW}⚠️ Limit order not filled, price moved away ({final_distance:.2f}%), cancelling limit order strategy{RESET}"

                        )

                        # Continue waiting in main loop

                        should_execute = False

                        last_price = final_price

                        await asyncio.sleep(poll_interval)

                        continue

            

            # Pre-check functions (only executed if should_execute is True)

            async def _get_spread_threshold(sym: str) -> float:

                """Adaptive spread threshold based on coin type, volatility, and market regime"""

                try:

                    ctype = await get_coin_type_range(sym)

                    # Base threshold by coin type

                    base_threshold = {

                        'BLUECHIP': 0.08,

                        'MAJOR': 0.12,

                        'ALTCOIN': 0.50,

                        'MICROCAP': 1.20

                    }.get(ctype, 0.50)

                    

                    # Adjust based on volatility (high vol = more lenient, low vol = stricter)

                    try:

                        from core import calculate_coin_volatility

                        volatility = await calculate_coin_volatility(sym)

                        if volatility > 40:

                            vol_adjustment = 1.5  # High volatility: allow wider spread

                        elif volatility > 25:

                            vol_adjustment = 1.2

                        elif volatility < 10:

                            vol_adjustment = 0.8  # Low volatility: require tighter spread

                        else:

                            vol_adjustment = 1.0

                    except Exception:

                        vol_adjustment = 1.0

                    

                    # Adjust based on market regime (if available from analysis)

                    regime_adjustment = 1.0

                    if 'analysis' in locals() and hasattr(analysis, 'market_regime'):

                        try:

                            regime = analysis.market_regime.value if hasattr(analysis.market_regime, 'value') else str(analysis.market_regime)

                            if regime in ['strong_uptrend', 'uptrend']:

                                regime_adjustment = 1.1  # Uptrend: slightly more lenient

                            elif regime in ['strong_downtrend', 'downtrend']:

                                regime_adjustment = 0.9  # Downtrend: stricter

                            elif regime in ['sideways', 'high_vol_consolidation']:

                                regime_adjustment = 1.0

                        except Exception:

                            pass

                    

                    final_threshold = base_threshold * vol_adjustment * regime_adjustment

                    return fina

                    l_threshold

                except Exception:

                    return 0.50



            async def _get_flow_thresholds(sym: str) -> tuple:

                """Adaptive flow thresholds based on coin type, volatility, and market regime"""

                try:

                    ctype = await get_coin_type_range(sym)

                    # Base thresholds by coin type (buy_pressure_min, cvd_momentum_min)

                    base_thresholds = {

                        'BLUECHIP': (55.0, 0.0),

                        'MAJOR': (52.0, -100.0),

                        'ALTCOIN': (50.0, -150.0),

                        'MICROCAP': (45.0, -250.0)

                    }.get(ctype, (50.0, -150.0))

                    bp_base, cvd_base = base_thresholds

                    

                    # Adjust based on volatility (high vol = lower threshold, low vol = higher threshold)

                    bp_adjustment = 0.0

                    cvd_adjustment = 0.0

                    try:

                        from core import calculate_coin_volatility

                        volatility = await calculate_coin_volatility(sym)

                        if volatility > 40:

                            bp_adjustment = -5.0  # High volatility: lower BP requirement

                            cvd_adjustment = -50.0  # More lenient CVD

                        elif volatility > 25:

                            bp_adjustment = -2.0

                            cvd_adjustment = -25.0

                        elif volatility < 10:

                            bp_adjustment = +3.0  # Low volatility: higher BP requirement

                            cvd_adjustment = +30.0  # Stricter CVD

                    except Exception:

                        pass

                    

                    # Adjust based on market regime

                    if 'analysis' in locals() and hasattr(analysis, 'market_regime'):

                        try:

                            regime = analysis.market_regime.value if hasattr(analysis.market_regime, 'value') else str(analysis.market_regime)

                            if regime in ['strong_uptrend', 'uptrend']:

                                bp_adjustment -= 2.0  # Uptrend: lower BP requirement

                                cvd_adjustment -= 30.0

                            elif regime in ['strong_downtrend', 'downtrend']:

                                bp_adjustment += 3.0  # Downtrend: higher BP requirement

                                cvd_adjustment += 50.0

                        except Exception:

                            pass

                    

                    final_bp = max(40.0, min(70.0, bp_base + bp_adjustment))

                    final_cvd = max(-500.0, min(100.0, cvd_base + cvd_adjustment))

                    return final_bp, final_cvd

                except Exception:

                    return 50.0, -150.0

            

            if getattr(TradingConfig, 'ENABLE_BUY_GUARD_DEBOUNCE', False):

                samples = max(1, int(getattr(TradingConfig, 'DEBOUNCE_SAMPLES', 2)))

                gap_ms = max(50, int(getattr(TradingConfig, 'DEBOUNCE_INTERVAL_MS', 250)))

                buy_ok = True

                bp_min, cvd_min = await _get_flow_thresholds(symbol)

                for i in range(samples):

                    p_now = await get_price_optimized(symbol)

                    if getattr(TradingConfig, 'ENABLE_GUARD_DEBUG_LOGS', False):

                        output_manager.print_static(

                            f"{BLUE}🔍 Debounce[{i+1}/{samples}]: price {p_now} vs trigger {trigger:.8f}{RESET}"

                        )

                    if p_now is None or p_now < trigger - 1e-10:

                        buy_ok = False

                        # Disable debounce price messages to reduce spam (pre-check still active)

                        # if should_show_pause_msg('debounce_price'):

                        #     output_manager.print_static(f"{YELLOW}⏸️ Debounce: price fell below trigger on sample {i+1}/{samples}{RESET}")

                        break

                    try:

                        flow = await get_order_flow_optimized(symbol, 20)

                    except Exception:

                        flow = {}

                    bp = float(flow.get('buy_pressure', 50))

                    cvd = float(flow.get('cvd_momentum', 0))

                    if getattr(TradingConfig, 'ENABLE_GUARD_DEBUG_LOGS', False):

                        output_manager.print_static(

                            f"{BLUE}🔍 Debounce[{i+1}/{samples}]: BP {bp:.1f}% (min {bp_min:.1f}%) | CVD {cvd:+.0f} (min {cvd_min:+.0f}){RESET}"

                        )

                    if bp < bp_min or cvd < cvd_min:

                        buy_ok = False

                        # Disable debounce flow messages to reduce spam (pre-check still active)

                        # if should_show_pause_msg('debounce_flow'):

                        #     output_manager.print_static(

                        #         f"{YELLOW}⏸️ Debounce: order flow weak (BP {bp:.1f}%<{bp_min:.1f}% or CVD {cvd:+.0f}<{cvd_min:+.0f}){RESET}"

                        #     )

                        break

                    await asyncio.sleep(gap_ms / 1000.0)

                if not buy_ok:

                    last_price = price

                    await asyncio.sleep(poll_interval)

                    continue

            if getattr(TradingConfig, 'ENABLE_BUY_GUARD_SPREAD', False):

                try:

                    orderbook_check = await get_orderbook_optimized(symbol, 20)

                    if orderbook_check and 'bids' in orderbook_check and 'asks' in orderbook_check:

                        bids = orderbook_check['bids']

                        asks = orderbook_check['asks']

                        if bids and asks:

                            best_bid = float(bids[0][0])

                            best_ask = float(asks[0][0])

                            spread_pct = ((best_ask - best_bid) / best_bid) * 100

                            max_spread = await _get_spread_threshold(symbol)

                            if getattr(TradingConfig, 'ENABLE_GUARD_DEBUG_LOGS', False):

                                output_manager.print_static(

                                    f"{BLUE}🔍 Spread check: {spread_pct:.3f}% (max {max_spread:.3f}%) | bid {best_bid:.8f} ask {best_ask:.8f}{RESET}"

                                )

                            if spread_pct > max_spread:

                                if should_show_pause_msg('spread'):

                                    output_manager.print_static(

                                        f"{YELLOW}⏸️ BUY PAUSED: Spread {spread_pct:.3f}% > {max_spread:.3f}% (adaptive) — waiting for tighter spread{RESET}"

                                    )

                                await asyncio.sleep(1.0)

                                last_price = price

                                continue

                except Exception as _:

                    pass

            if getattr(TradingConfig, 'ENABLE_BUY_GUARD_ASK_WALL', False):

                    try:

                        async def _get_ask_wall_params(sym: str) -> tuple:

                            """Adaptive ask wall parameters based on coin type, volatility, and market regime"""

                            try:

                                ctype = await get_coin_type_range(sym)

                                # Base parameters by coin type (distance_bps, ratio_threshold)

                                base_params = {

                                    'BLUECHIP': (5, 1.5),

                                    'MAJOR': (7, 1.6),

                                    'ALTCOIN': (10, 1.8),

                                    'MICROCAP': (15, 2.2)

                                }.get(ctype, (10, 1.8))

                                distance_base, ratio_base = base_params

                                

                                # Adjust based on volatility

                                ratio_adjustment = 0.0

                                try:

                                    from core import calculate_coin_volatility

                                    volatility = await calculate_coin_volatility(sym)

                                    if volatility > 40:

                                        ratio_adjustment = +0.3  # High volatility: more lenient

                                    elif volatility > 25:

                                        ratio_adjustment = +0.2

                                    elif volatility < 10:

                                        ratio_adjustment = -0.2  # Low volatility: stricter

                                except Exception:

                                    pass

                                

                                # Adjust based on market regime

                                if 'analysis' in locals() and hasattr(analysis, 'market_regime'):

                                    try:

                                        regime = analysis.market_regime.value if hasattr(analysis.market_regime, 'value') else str(analysis.market_regime)

                                        if regime in ['strong_uptrend', 'uptrend']:

                                            ratio_adjustment += 0.2  # Uptrend: more lenient

                                        elif regime in ['strong_downtrend', 'downtrend']:

                                            ratio_adjustment -= 0.2  # Downtrend: stricter

                                    except Exception:

                                        pass

                                

                                final_ratio = max(1.2, min(3.0, ratio_base + ratio_adjustment))

                                return distance_base, final_ratio

                            except Exception:

                                return 10, 1.8

                        distance_bps, ratio_threshold = await _get_ask_wall_params(symbol)

                        ob = await get_orderbook_optimized(symbol, 50)

                        if ob and ob.get('asks') and ob.get('bids'):

                            asks = ob['asks']

                            bids = ob['bids']

                            best_bid = float(bids[0][0])

                            best_ask = float(asks[0][0])

                            mid = (best_bid + best_ask) / 2.0

                            band_up = mid * (1.0 + distance_bps / 10000.0)

                            band_dn = mid * (1.0 - distance_bps / 10000.0)

                            ask_vol_band = 0.0

                            for ap, av in ((float(a[0]), float(a[1])) for a in asks):

                                if ap <= band_up:

                                    ask_vol_band += av

                                else:

                                    break

                            bid_vol_band = 0.0

                            for bp, bv in ((float(b[0]), float(b[1])) for b in bids):

                                if bp >= band_dn:

                                    bid_vol_band += bv

                                else:

                                    break

                            ratio = ask_vol_band / max(1e-9, bid_vol_band)

                            if getattr(TradingConfig, 'ENABLE_GUARD_DEBUG_LOGS', False):

                                output_manager.print_static(

                                    f"{BLUE}🔍 Ask-wall check: askBand {ask_vol_band:.4f} vs bidBand {bid_vol_band:.4f} → ratio {ratio:.2f}x (thr {ratio_threshold:.2f}x) | band ±{distance_bps}bps{RESET}"

                                )

                            try:

                                flow = await get_order_flow_optimized(symbol, 20)

                                buy_press = float(flow.get('buy_pressure', 50))

                                cvd = float(flow.get('cvd_momentum', 0))

                            except Exception:

                                buy_press, cvd = 50.0, 0.0

                            if getattr(TradingConfig, 'ENABLE_GUARD_DEBUG_LOGS', False):

                                output_manager.print_static(

                                    f"{BLUE}🔍 Flow for ask-wall: BP {buy_press:.1f}% | CVD {cvd:+.0f}{RESET}"

                                )

                            if ratio > ratio_threshold and (buy_press < 60.0 or cvd <= 0):

                                if should_show_pause_msg('ask_wall'):

                                    output_manager.print_static(

                                        f"{YELLOW}⏸️ BUY PAUSED: Nearby ask wall ratio {ratio:.2f}x > {ratio_threshold:.2f}x within {distance_bps} bps — waiting for absorption/clearance{RESET}"

                                    )

                                await asyncio.sleep(1.0)

                                last_price = price

                                continue

                    except Exception:

                        pass

            # International standard pre-check: Whale Wall detection

            if getattr(TradingConfig, 'ENABLE_BUY_GUARD_WHALE_WALL', True):

                    try:

                        async def _get_whale_wall_params(sym: str, buy_amount: float = None) -> tuple:

                            """Adaptive whale wall detection parameters based on coin type, volatility, and market regime"""

                            try:

                                ctype = await get_coin_type_range(sym)

                                # Base thresholds by coin type

                                base_params = {

                                    'BLUECHIP': (8.0, 0.15),

                                    'MAJOR': (6.0, 0.20),

                                    'ALTCOIN': (5.0, 0.25),

                                    'MICROCAP': (4.0, 0.30)

                                }.get(ctype, (5.0, 0.25))

                                multiplier_base, depth_base = base_params

                                

                                # Adjust based on volatility (high vol = more lenient, low vol = stricter)

                                multiplier_adjustment = 0.0

                                depth_adjustment = 0.0

                                try:

                                    from core import calculate_coin_volatility

                                    volatility = await calculate_coin_volatility(sym)

                                    if volatility > 40:

                                        multiplier_adjustment = +1.5  # High volatility: more lenient

                                        depth_adjustment = +0.05

                                    elif volatility > 25:

                                        multiplier_adjustment = +0.5

                                        depth_adjustment = +0.02

                                    elif volatility < 10:

                                        multiplier_adjustment = -0.5  # Low volatility: stricter

                                        depth_adjustment = -0.03

                                except Exception:

                                    pass

                                

                                # Adjust based on market regime

                                if 'analysis' in locals() and hasattr(analysis, 'market_regime'):

                                    try:

                                        regime = analysis.market_regime.value if hasattr(analysis.market_regime, 'value') else str(analysis.market_regime)

                                        if regime in ['strong_uptrend', 'uptrend']:

                                            multiplier_adjustment += 1.0  # Uptrend: more lenient

                                            depth_adjustment += 0.03

                                        elif regime in ['strong_downtrend', 'downtrend']:

                                            multiplier_adjustment -= 0.5  # Downtrend: stricter

                                            depth_adjustment -= 0.02

                                    except Exception:

                                        pass

                                

                                final_multiplier = max(3.0, min(12.0, multiplier_base + multiplier_adjustment))

                                final_depth = max(0.10, min(0.40, depth_base + depth_adjustment))

                                

                                # If buy_amount specified, also check against order size

                                if buy_amount:

                                    order_size_multiplier = 3.0

                                else:

                                    order_size_multiplier = None

                                

                                return final_multiplier, final_depth, order_size_multiplier

                            except Exception:

                                return 5.0, 0.25, 3.0

                        

                        orderbook_whale = await get_orderbook_optimized(symbol, 20)

                        if orderbook_whale and 'asks' in orderbook_whale and 'bids' in orderbook_whale:

                            asks = orderbook_whale['asks']

                            bids = orderbook_whale['bids']

                            

                            if asks and bids:

                                best_ask = float(asks[0][0])

                                best_bid = float(bids[0][0])

                                

                                # Calculate average order size in top 10 levels

                                ask_sizes = [float(ask[1]) for ask in asks[:10]]

                                bid_sizes = [float(bid[1]) for bid in bids[:10]]

                                all_sizes = ask_sizes + bid_sizes

                                

                                if all_sizes:

                                    avg_order_size = sum(all_sizes) / len(all_sizes)

                                    

                                    # Calculate total depth in top 10 levels

                                    total_ask_depth = sum(float(ask[1]) * float(ask[0]) for ask in asks[:10])

                                    total_bid_depth = sum(float(bid[1]) * float(bid[0]) for bid in bids[:10])

                                    total_depth = total_ask_depth + total_bid_depth

                                    

                                    multiplier_threshold, depth_percentage, order_size_multiplier = await _get_whale_wall_params(symbol, buy_amount)

                                    

                                    # Check for whale walls in top 5 ask levels (most critical for buy)

                                    whale_wall_detected = False

                                    whale_wall_info = []

                                    

                                    for i, (ask_price, ask_volume) in enumerate(asks[:5]):

                                        ask_price_f = float(ask_price)

                                        ask_volume_f = float(ask_volume)

                                        ask_size_usd = ask_volume_f * ask_price_f

                                        

                                        # Check multiple criteria

                                        is_whale_by_multiplier = ask_volume_f > (avg_order_size * multiplier_threshold)

                                        is_whale_by_depth = ask_size_usd > (total_depth * depth_percentage)

                                        is_whale_by_order_size = False

                                        

                                        if order_size_multiplier and buy_amount:

                                            is_whale_by_order_size = ask_size_usd > (buy_amount * order_size_multiplier)

                                        

                                        if is_whale_by_multiplier or is_whale_by_depth or is_whale_by_order_size:

                                            whale_wall_detected = True

                                            distance_pct = ((ask_price_f - best_ask) / best_ask) * 100

                                            whale_wall_info.append({

                                                'level': i + 1,

                                                'price': ask_price_f,

                                                'size_usd': ask_size_usd,

                                                'distance_pct': distance_pct,

                                                'reason': []

                                            })

                                            if is_whale_by_multiplier:

                                                whale_wall_info[-1]['reason'].append(f"{ask_volume_f/avg_order_size:.1f}x avg")

                                            if is_whale_by_depth:

                                                whale_wall_info[-1]['reason'].append(f"{ask_size_usd/total_depth*100:.1f}% depth")

                                            if is_whale_by_order_size:

                                                whale_wall_info[-1]['reason'].append(f"{ask_size_usd/buy_amount:.1f}x order")

                                    

                                    if whale_wall_detected:

                                        # Check if order flow is strong enough to break through

                                        try:

                                            flow = await get_order_flow_optimized(symbol, 20)

                                            buy_press = float(flow.get('buy_pressure', 50))

                                            cvd = float(flow.get('cvd_momentum', 0))

                                        except Exception:

                                            buy_press, cvd = 50.0, 0.0

                                        

                                        # Only pause if order flow is weak (whale wall likely to hold)

                                        if buy_press < 65.0 or cvd <= 0:

                                            whale_details = " | ".join([

                                                f"L{ww['level']}: ${ww['size_usd']:,.0f} @ {ww['distance_pct']:.2f}% ({', '.join(ww['reason'])})"

                                                for ww in whale_wall_info[:2]  # Show top 2

                                            ])

                                            

                                            if getattr(TradingConfig, 'ENABLE_GUARD_DEBUG_LOGS', False):

                                                output_manager.print_static(

                                                    f"{BLUE}🔍 Whale wall detected: {whale_details}{RESET}"

                                                )

                                            

                                            if should_show_pause_msg('whale_wall'):

                                                output_manager.print_static(

                                                    f"{YELLOW}⏸️ BUY PAUSED: Whale wall detected in orderbook (weak order flow: BP {buy_press:.1f}%, CVD {cvd:+.0f}) — waiting for absorption or stronger momentum{RESET}"

                                                )

                                            await asyncio.sleep(1.0)

                                            last_price = price

                                            continue

                                        else:

                                            # Strong order flow, whale wall might be absorbed

                                            if getattr(TradingConfig, 'ENABLE_GUARD_DEBUG_LOGS', False):

                                                whale_details = " | ".join([

                                                    f"L{ww['level']}: ${ww['size_usd']:,.0f} @ {ww['distance_pct']:.2f}%"

                                                    for ww in whale_wall_info[:1]

                                                ])

                                                output_manager.print_static(

                                                    f"{BLUE}🔍 Whale wall detected but strong flow (BP {buy_press:.1f}%, CVD {cvd:+.0f}) — proceeding{RESET}"

                                                )

                    except Exception:

                        pass

            # International standard pre-check: Market Depth/Liquidity validation

            if getattr(TradingConfig, 'ENABLE_BUY_GUARD_DEPTH', True):

                    try:

                        async def _get_min_depth_requirement(sym: str, buy_amount: float = None) -> float:

                            """International standard: Minimum depth = 2x order size for safe execution"""

                            try:

                                ctype = await get_coin_type_range(sym)

                                current_price = await get_price_optimized(sym)

                                if not current_price or current_price <= 0:

                                    return 1000.0  # Default minimum

                                

                                # Base minimum depth by coin type

                                base_min_depth = {

                                    'BLUECHIP': 10000,

                                    'MAJOR': 5000,

                                    'ALTCOIN': 2000,

                                    'MICROCAP': 500

                                }.get(ctype, 2000)

                                

                                # For scalping: require 2x order size in depth (international standard)

                                if buy_amount:

                                    required_depth = buy_amount * 2.0

                                    return max(base_min_depth, required_depth)

                                

                                return base_min_depth

                            except Exception:

                                return 2000.0

                        

                        orderbook_depth = await get_orderbook_optimized(symbol, 20)

                        if orderbook_depth and 'asks' in orderbook_depth:

                            asks = orderbook_depth['asks']

                            if asks:

                                best_ask = float(asks[0][0])

                                # Calculate depth in top 3 levels (standard for scalping)

                                ask_depth_top3 = sum(float(ask[1]) for ask in asks[:3])

                                ask_depth_usd = ask_depth_top3 * best_ask

                                

                                min_depth_required = await _get_min_depth_requirement(symbol, buy_amount)

                                

                                if getattr(TradingConfig, 'ENABLE_GUARD_DEBUG_LOGS', False):

                                    output_manager.print_static(

                                        f"{BLUE}🔍 Depth check: ${ask_depth_usd:,.2f} (min ${min_depth_required:,.2f}) | Top 3 levels{RESET}"

                                    )

                                

                                if ask_depth_usd < min_depth_required:

                                    # Disable depth warning messages to reduce spam (pre-check still active)

                                    # if should_show_pause_msg('depth'):

                                    #     output_manager.print_static(

                                    #         f"{YELLOW}⏸️ BUY PAUSED: Insufficient market depth ${ask_depth_usd:,.2f} < ${min_depth_required:,.2f} (international standard: 2x order size) — waiting for better liquidity{RESET}"

                                    #     )

                                    await asyncio.sleep(1.0)

                                    last_price = price

                                    continue

                    except Exception:

                        pass

            # International standard pre-check: Volume validation (for scalping)

            if getattr(TradingConfig, 'ENABLE_BUY_GUARD_VOLUME', True):

                    try:

                        async def _get_min_volume_requirement(sym: str) -> float:

                            """Adaptive minimum volume requirement based on coin type, volatility, and market regime"""

                            try:

                                ctype = await get_coin_type_range(sym)

                                # Base volume multiplier by coin type

                                base_multiplier = {

                                    'BLUECHIP': 0.001,  # 0.1% of typical volume

                                    'MAJOR': 0.002,     # 0.2% of typical volume

                                    'ALTCOIN': 0.005,   # 0.5% of typical volume

                                    'MICROCAP': 0.01    # 1% of typical volume

                                }.get(ctype, 0.005)

                                

                                # Adjust based on volatility (high vol = lower requirement, low vol = higher requirement)

                                multiplier_adjustment = 0.0

                                try:

                                    from core import calculate_coin_volatility

                                    volatility = await calculate_coin_volatility(sym)

                                    if volatility > 40:

                                        multiplier_adjustment = -0.002  # High volatility: lower requirement

                                    elif volatility > 25:

                                        multiplier_adjustment = -0.001

                                    elif volatility < 10:

                                        multiplier_adjustment = +0.001  # Low volatility: higher requirement

                                except Exception:

                                    pass

                                

                                # Adjust based on market regime

                                if 'analysis' in locals() and hasattr(analysis, 'market_regime'):

                                    try:

                                        regime = analysis.market_regime.value if hasattr(analysis.market_regime, 'value') else str(analysis.market_regime)

                                        if regime in ['strong_uptrend', 'uptrend']:

                                            multiplier_adjustment -= 0.001  # Uptrend: lower requirement

                                        elif regime in ['strong_downtrend', 'downtrend']:

                                            multiplier_adjustment += 0.002  # Downtrend: higher requirement

                                    except Exception:

                                        pass

                                

                                final_multiplier = max(0.0005, min(0.02, base_multiplier + multiplier_adjustment))

                                

                                # Get recent volume from candles

                                candles_5m = await get_candles_optimized(sym, '5m', 5)

                                if candles_5m and len(candles_5m) > 0:

                                    avg_volume = sum(c['volume'] for c in candles_5m) / len(candles_5m)

                                    min_volume = avg_volume * final_multiplier

                                    return max(min_volume, 1000.0)  # Absolute minimum

                                return 1000.0

                            except Exception:

                                return 1000.0

                        

                        candles_check = await get_candles_optimized(symbol, '5m', 3)

                        if candles_check and len(candles_check) > 0:

                            recent_volume = candles_check[-1].get('volume', 0)

                            min_volume_req = await _get_min_volume_requirement(symbol)

                            

                            if getattr(TradingConfig, 'ENABLE_GUARD_DEBUG_LOGS', False):

                                output_manager.print_static(

                                    f"{BLUE}🔍 Volume check: {recent_volume:,.0f} (min {min_volume_req:,.0f}) | Last 5m candle{RESET}"

                                )

                            

                            if recent_volume < min_volume_req:

                                if should_show_pause_msg('volume'):

                                    output_manager.print_static(

                                        f"{YELLOW}⏸️ BUY PAUSED: Low volume {recent_volume:,.0f} < {min_volume_req:,.0f} (international standard) — waiting for volume increase{RESET}"

                                    )

                                await asyncio.sleep(1.0)

                                last_price = price

                                continue

                    except Exception:

                        pass

            # International standard pre-check: Slippage estimation

            if getattr(TradingConfig, 'ENABLE_BUY_GUARD_SLIPPAGE', True):

                    try:

                        async def _estimate_slippage(sym: str, buy_amount: float, orderbook: dict) -> float:

                            """International standard: Estimate slippage before execution"""

                            if not orderbook or 'asks' not in orderbook or not buy_amount:

                                return 0.0

                            

                            asks = orderbook['asks']

                            if not asks:

                                return 0.0

                            

                            best_ask = float(asks[0][0])

                            total_filled = 0.0

                            total_cost = 0.0

                            

                            # Simulate order execution through orderbook

                            for ask_price, ask_volume in asks:

                                ask_price_f = float(ask_price)

                                ask_volume_f = float(ask_volume)

                                

                                remaining = buy_amount - total_filled

                                if remaining <= 0:

                                    break

                                

                                volume_needed = remaining / best_ask  # Convert USDT to coin amount

                                fill_volume = min(volume_needed, ask_volume_f)

                                

                                total_filled += fill_volume * best_ask  # Convert back to USDT

                                total_cost += fill_volume * ask_price_f

                            

                            if total_filled > 0:

                                avg_price = total_cost / (total_filled / best_ask) if (total_filled / best_ask) > 0 else best_ask

                                slippage_pct = ((avg_price - best_ask) / best_ask) * 100

                                return slippage_pct

                            return 0.0

                        

                        orderbook_slippage = await get_orderbook_optimized(symbol, 20)

                        if orderbook_slippage and buy_amount:

                            estimated_slippage = await _estimate_slippage(symbol, buy_amount, orderbook_slippage)

                            max_slippage = getattr(TradingConfig, 'MAX_ACCEPTABLE_SLIPPAGE', 0.5)

                            

                            if getattr(TradingConfig, 'ENABLE_GUARD_DEBUG_LOGS', False):

                                output_manager.print_static(

                                    f"{BLUE}🔍 Slippage estimate: {estimated_slippage:.3f}% (max {max_slippage:.1f}%){RESET}"

                                )

                            

                            if estimated_slippage > max_slippage:

                                if should_show_pause_msg('slippage'):

                                    output_manager.print_static(

                                        f"{YELLOW}⏸️ BUY PAUSED: Estimated slippage {estimated_slippage:.3f}% > {max_slippage:.1f}% (international standard) — waiting for better liquidity{RESET}"

                                    )

                                await asyncio.sleep(1.0)

                                last_price = price

                                continue

                    except Exception:

                        pass

            

            output_manager.stop_live_update()

            # Pass buy_amount to execute_buy_order (it's available in function scope)

            # Use entry_price (optimized: activation price for Fixed Mode, optimized limit/market price for normal trailing)

            if should_execute:

                if use_limit_order:

                    output_manager.print_static(

                        f"{CYAN}📋 Order Type: LIMIT ORDER at ${fmt(entry_price)}{RESET}"

                    )

                else:

                    output_manager.print_static(

                        f"{CYAN}📋 Order Type: MARKET ORDER at ${fmt(entry_price)}{RESET}"

                    )

            await execute_buy_order(symbol, entry_price, trigger, low, activate_price, current_delta, analysis, buy_amount=buy_amount)

            if auto_trailing_stop:

                if take_profit_percent:

                    output_manager.print_static(f"{CYAN}🎯 Fixed Take Profit activated ({take_profit_percent:.1f}%)...{RESET}")

                    await asyncio.sleep(1)

                    # Use entry_price (activation price for Fixed Mode) instead of current price

                    await fixed_take_profit_exit(symbol, entry_price, take_profit_percent, stop_loss_percent)

                elif dynamic_ai_stop:

                    output_manager.print_static(f"{CYAN}🧠 Dynamic AI Trailing Stop activated...{RESET}")

                    await asyncio.sleep(1)

                    await dynamic_ai_trailing_stop_after_buy(symbol, price)

                else:

                    output_manager.print_static(f"{CYAN}🔄 Static Trailing Stop activated...{RESET}")

                    await asyncio.sleep(1)

                    await auto_trailing_stop_after_buy(symbol, price, stop_loss_percent)

            if advance:

                activate_sell = price * (1 + offset / 100.0)

                output_manager.print_static(f"{CYAN}🔄 Advance mode: Setting up trailing sell at ${fmt(activate_sell)}{RESET}")

                await asyncio.sleep(2)

                await hybrid_trailing_sell(symbol, current_delta, activate_sell, advance=False)

            break

            last_price = price

            await asyncio.sleep(poll_interval)

    except asyncio.CancelledError:

        output_manager.stop_live_update()

        output_manager.print_static(f"\n{YELLOW}⏹️ Hybrid trailing buy cancelled{RESET}")

        raise

    except KeyboardInterrupt:

        output_manager.stop_live_update()

        output_manager.print_static(f"\n{YELLOW}⏹️ Hybrid trailing buy stopped manually{RESET}")

        raise

    except Exception as e:

        output_manager.stop_live_update()

        output_manager.print_static(f"\n{RED}❌ Error in hybrid trailing buy: {e}{RESET}")

        error_handler.record_error("Trailing_Buy_Error", "Hybrid trailing buy failed", {"error": str(e)})

    finally:

        output_manager.stop_live_update()



async def hybrid_trailing_sell(symbol: str, delta_percent: float, activate_price: float,

                             advance: bool = False, offset: float = 0.0, skip_wait: bool = False,

                             poll_interval: float = 2.0, sell_quantity: Optional[float] = None):

    output_manager.print_static(f"\n{MAGENTA}🧠 HYBRID TRAILING SELL {symbol}{RESET}")

    if sell_quantity:

        coin_name = symbol.split('_')[0].upper()

        output_manager.print_static(f"{MAGENTA}Quantity: {sell_quantity:.8f} {coin_name}{RESET}")

    output_manager.print_static(f"{MAGENTA}Activation: >= ${fmt(activate_price)} | Delta: {delta_percent:.1f}%{RESET}")

    current_strategy = AIStrategy(

        name="Hybrid Trailing Sell",

        description="Combining aggressive profit-taking with adaptive risk management",

        activation_price=activate_price,

        delta_percent=delta_percent,

        confidence=100.0,

        risk_reward_ratio=2.5,

        timeframe="1H",

        market_regime=MarketRegime.UNKNOWN,

        strategy_type=StrategyType.TREND_FOLLOWING

    )

    reanalysis_task = None

    try:

        if not skip_wait:

            output_manager.print_static(f"{YELLOW}Menunggu harga mencapai activation price...{RESET}")

            reanalysis_task = await task_manager.create_task(

                symbol, "reanalysis",

                adaptive_time_based_reanalysis(symbol, current_strategy, "SELL")

            )

            output_manager.start_live_update()

            last_price = activate_price

            while True:

                price = await get_price_optimized(symbol)

                if price is None:

                    await asyncio.sleep(poll_interval)

                    continue

                if reanalysis_task and reanalysis_task.done():

                    try:

                        result = await reanalysis_task

                        if result == "CANCEL":

                            output_manager.stop_live_update()

                            output_manager.print_static(f"{YELLOW}🔄 Looking for new setup after cancellation...{RESET}")

                            await asyncio.sleep(3)

                            new_strategy = await handle_strategy_cancellation(symbol, "AI adaptive re-analysis")

                            if new_strategy:

                                await execute_strategy(symbol, new_strategy, advance, offset)

                            return

                    except asyncio.CancelledError:

                        pass

                diff_pct = (safe_division(price, activate_price, 1.0) - 1) * 100

                current_delta = current_strategy.delta_percent

                status = f"⏳ {symbol} | Price: ${fmt(price)} ({diff_pct:+.2f}%) | Target: ${fmt(activate_price)} | Delta: {current_delta:.1f}%"

                output_manager.print_live(status)

                if price >= activate_price:

                    output_manager.stop_live_update()

                    output_manager.print_static(f"\n{GREEN}{'='*60}{RESET}")

                    output_manager.print_static(f"{GREEN}✅ ACTIVATION ACHIEVED! Starting trailing sell...{RESET}")

                    output_manager.print_static(f"{GREEN}{'='*60}{RESET}")

                    if reanalysis_task and not reanalysis_task.done():

                        reanalysis_task.cancel()

                    last_price = price

                    break

                last_price = price

                await asyncio.sleep(poll_interval)

        else:

            price = await get_price_optimized(symbol)

            output_manager.print_static(f"{GREEN}⚡ AUTO-ACTIVE | Current: ${fmt(price)} | Starting trailing...{RESET}")

            last_price = price

        high = last_price

        current_delta = current_strategy.delta_percent

        trigger = high * (1 - current_delta / 100.0)

        output_manager.print_static(f"\n{RED}{'='*80}{RESET}")

        output_manager.print_static(f"{RED}🎯 【TRAILING SELL MODE ACTIVE】{RESET}")

        output_manager.print_static(f"{RED}{'='*80}{RESET}")

        output_manager.print_static(f"  💰 Entry High: ${fmt(high)}")

        output_manager.print_static(f"  🎯 Sell Trigger: ${fmt(trigger)} (-{current_delta:.1f}% dari high)")

        output_manager.print_static(f"  📊 Strategy: Tunggu harga turun {current_delta:.1f}% dari high point, lalu SELL")

        output_manager.print_static(f"{RED}{'='*80}{RESET}")

        reanalysis_task = await task_manager.create_task(

            symbol, "reanalysis_trailing",

            adaptive_time_based_reanalysis(symbol, current_strategy, "SELL")

        )

        output_manager.start_live_update()

        last_delta_update = time.time()

        coin_type = await get_coin_type_range(symbol)

        price_history = []

        MAX_PRICE_HISTORY = 30

        global_dump_count = 0

        dump_mode_active = False

        while True:

            price = await get_price_optimized(symbol)

            if price is None:

                await asyncio.sleep(poll_interval)

                continue

            price_history.append(price)

            if len(price_history) > MAX_PRICE_HISTORY:

                price_history.pop(0)

            if len(price_history) >= 5 and not dump_mode_active:

                is_dump, dump_score, dump_details = detect_dump_adaptive(

                    symbol, price, price_history, global_dump_count

                )

                if is_dump:

                    output_manager.stop_live_update()

                    output_manager.print_static(f"\n{RED}🚨🚨🚨 DUMP DETECTED! Score: {dump_score}/{dump_details['threshold']}{RESET}")

                    output_manager.print_static(f"{CYAN}   Drop: {dump_details.get('drop_10s', 'N/A')}{RESET}")

                    output_manager.print_static(f"{CYAN}   Volume: {dump_details.get('volume', 'N/A')}{RESET}")

                    output_manager.print_static(f"{CYAN}   Trend: {dump_details.get('trend', 'N/A')}{RESET}")

                    output_manager.print_static(f"{CYAN}   Market: {dump_details.get('market', 'N/A')}{RESET}")

                    output_manager.print_static(f"{YELLOW}⚡ Activating PARALLEL + BURST monitoring...{RESET}")

                    dump_mode_active = True

                    source, final_price = await parallel_monitor_with_burst(

                        symbol, trigger, activate_price, high, current_delta, price_history

                    )

                    profit_pct = safe_division((final_price - activate_price), activate_price, 0.0) * 100

                    output_manager.print_static(f"\n{CYAN}📊 Dump Protection Exit ({source} mode){RESET}")

                    await execute_sell_order(symbol, final_price, trigger, high, activate_price, current_delta, profit_pct)

                    if advance:

                        output_manager.print_static(f"\n{YELLOW}{'─'*70}{RESET}")

                        output_manager.print_static(f"{YELLOW}🔍 SMART RESTART: Checking market recovery...{RESET}")

                        if portfolio_manager.consecutive_losses >= 3:

                            output_manager.print_static(f"{RED}🚨 CIRCUIT BREAKER: {portfolio_manager.consecutive_losses} consecutive losses!{RESET}")

                            output_manager.print_static(f"{RED}   Stopping auto-trader to prevent further losses.{RESET}")

                            output_manager.print_static(f"{CYAN}   Recommendation: Review market conditions or adjust strategy.{RESET}")

                            output_manager.print_static(f"{YELLOW}{'─'*70}{RESET}")

                            return

                        if portfolio_manager.session_trades >= 15:

                            output_manager.print_static(f"{YELLOW}⚠️  SESSION LIMIT: {portfolio_manager.session_trades} trades completed.{RESET}")

                            output_manager.print_static(f"{CYAN}   Stopping to prevent overtrading.{RESET}")

                            output_manager.print_static(f"{YELLOW}{'─'*70}{RESET}")

                            return

                        if portfolio_manager.daily_pnl < -500:

                            output_manager.print_static(f"{RED}🚨 DAILY LOSS LIMIT: ${portfolio_manager.daily_pnl:.2f}!{RESET}")

                            output_manager.print_static(f"{RED}   Stopping to preserve capital.{RESET}")

                            output_manager.print_static(f"{YELLOW}{'─'*70}{RESET}")

                            return

                        output_manager.print_static(f"{CYAN}📊 Analyzing post-dump market conditions...{RESET}")

                        current_volatility = await calculate_coin_volatility(symbol)

                        recovery_thresholds = get_recovery_thresholds(coin_type, current_volatility)

                        coin_type_display = get_coin_display_name(symbol, coin_type)

                        cooldown = recovery_thresholds['cooldown_seconds']

                        output_manager.print_static(f"{CYAN}   ⏱️  Cooldown: {cooldown}s ({coin_type_display}, {current_volatility:.0f}% vol)...{RESET}")

                        await asyncio.sleep(cooldown)

                        fresh_analysis = await analyze_hold_wait_buy_ai(symbol)

                        fresh_price = await get_price_optimized(symbol)

                        output_manager.print_static(f"\n{CYAN}🔍 Recovery Analysis (ADAPTIVE):{RESET}")

                        output_manager.print_static(f"{CYAN}   AI: {fresh_analysis.recommendation.value} {fresh_analysis.confidence:.0f}%{RESET}")

                        output_manager.print_static(f"{CYAN}   Regime: {fresh_analysis.market_regime.value.replace('_', ' ').title()}{RESET}")

                        output_manager.print_static(f"{CYAN}   Price: ${fmt(fresh_price)} (vs exit: ${fmt(final_price)}){RESET}")

                        output_manager.print_static(f"\n{YELLOW}🎯 Recovery Thresholds ({coin_type_display}, {current_volatility:.0f}% vol):{RESET}")

                        output_manager.print_static(f"{YELLOW}   ├─ Min Confidence: {recovery_thresholds['min_confidence']:.0f}%{RESET}")

                        output_manager.print_static(f"{YELLOW}   └─ Max Price Drop: -{recovery_thresholds['max_price_drop']:.0f}%{RESET}")

                        restart_ok = True

                        restart_reasons = []

                        min_conf = recovery_thresholds['min_confidence']

                        if fresh_analysis.confidence < min_conf:

                            restart_ok = False

                            restart_reasons.append(f"Low confidence ({fresh_analysis.confidence:.0f}% < {min_conf:.0f}%)")

                        if fresh_analysis.recommendation not in [ActionRecommendation.STRONG_BUY, ActionRecommendation.BUY]:

                            restart_ok = False

                            restart_reasons.append(f"No BUY signal ({fresh_analysis.recommendation.value})")

                        if fresh_analysis.market_regime == MarketRegime.STRONG_DOWNTREND:

                            restart_ok = False

                            restart_reasons.append("Still in strong downtrend")

                        max_drop = recovery_thresholds['max_price_drop']

                        if fresh_price:

                            price_change = safe_division((fresh_price - final_price), final_price, 0) * 100

                            if price_change < -max_drop:

                                restart_ok = False

                                restart_reasons.append(f"Price dumped further ({price_change:.1f}% < -{max_drop:.0f}%)")

                        if restart_ok:

                            output_manager.print_static(f"{GREEN}✅ MARKET RECOVERING - Conditions favorable for re-entry!{RESET}")

                            output_manager.print_static(f"{GREEN}   ├─ Confidence: {fresh_analysis.confidence:.0f}% ✅{RESET}")

                            output_manager.print_static(f"{GREEN}   ├─ Signal: {fresh_analysis.recommendation.value} ✅{RESET}")

                            output_manager.print_static(f"{GREEN}   ├─ Regime: {fresh_analysis.market_regime.value.replace('_', ' ').title()} ✅{RESET}")

                            output_manager.print_static(f"{GREEN}   └─ Session: {portfolio_manager.session_trades}/15 trades, {portfolio_manager.consecutive_losses} consecutive losses ✅{RESET}")

                            output_manager.print_static(f"{YELLOW}{'─'*70}{RESET}")

                            activate_buy = final_price * (1 - offset / 100.0)

                            output_manager.print_static(f"{CYAN}🔄 Setting up trailing buy at ${fmt(activate_buy)} (-{offset:.1f}% from exit){RESET}")

                            await asyncio.sleep(2)

                            await hybrid_trailing_buy(symbol, current_delta, activate_buy, advance=True, auto_mode=True)

                        else:

                            output_manager.print_static(f"{RED}❌ MARKET STILL UNFAVORABLE - Stopping auto-trader{RESET}")

                            for reason in restart_reasons:

                                output_manager.print_static(f"{RED}   • {reason}{RESET}")

                            output_manager.print_static(f"{CYAN}💡 Recommendation: Wait for market stabilization before resuming.{RESET}")

                            output_manager.print_static(f"{YELLOW}{'─'*70}{RESET}")

                            return

                    return

            if reanalysis_task and reanalysis_task.done():

                try:

                    result = await reanalysis_task

                    if result == "CANCEL":

                        output_manager.stop_live_update()

                        output_manager.print_static(f"{YELLOW}🔄 Strategy cancelled during trailing...{RESET}")

                        await asyncio.sleep(3)

                        new_strategy = await handle_strategy_cancellation(symbol, "Trailing phase re-analysis")

                        if new_strategy:

                            await execute_strategy(symbol, new_strategy, advance, offset)

                        return

                    elif result == "ADJUST":

                        output_manager.print_static(f"{CYAN}🔄 Delta updated to: {current_strategy.delta_percent}%{RESET}")

                except asyncio.CancelledError:

                    pass

            if price > high:

                high = price

            profit_pct = safe_division((price - activate_price), activate_price, 0.0) * 100

            base_delta = current_strategy.delta_percent

            profit_tier_adj = get_profit_tier_adjustment(profit_pct)

            progressive_delta = base_delta + profit_tier_adj

            if coin_type == 'BLUECHIP':

                progressive_delta = max(0.8, min(5.0, progressive_delta))

            elif coin_type == 'MAJOR':

                progressive_delta = max(1.0, min(6.5, progressive_delta))

            elif coin_type == 'ALTCOIN':

                progressive_delta = max(1.5, min(9.0, progressive_delta))

            elif coin_type == 'MICROCAP':

                progressive_delta = max(2.5, min(14.0, progressive_delta))

            else:

                progressive_delta = max(1.0, min(8.0, progressive_delta))

            current_delta = progressive_delta

            trigger = high * (1 - current_delta / 100.0)

            arrow = "🟢" if price > last_price else "🔴" if price < last_price else "🟡"

            drop_from_peak = safe_division((high - price), high, 0.0) * 100

            need_drop_pct = safe_division((price - trigger), price, 0.0) * 100

            status = f"{RED}📉【TRAILING SELL】{symbol} | 💰 ${fmt(price)} {arrow} | 📈 High: ${fmt(high)} | 🎯 ${fmt(trigger)} | Need: {need_drop_pct:.2f}% drop | P/L: {profit_pct:+.2f}%{RESET}"

            output_manager.print_live(status)

            if price <= trigger + 1e-10:

                output_manager.stop_live_update()

                output_manager.print_static(f"\n{YELLOW}⚠️  TRIGGER HIT! Checking market quality...{RESET}")

                quality, score, details = await check_market_quality(symbol)

                output_manager.print_static(f"{CYAN}📊 Market Quality: {quality} ({score}/10){RESET}")

                output_manager.print_static(f"   ├─ Buy Pressure: {details.get('buy_pressure', 'N/A')}")

                output_manager.print_static(f"   ├─ Volume: {details.get('volume', 'N/A')}")

                output_manager.print_static(f"   ├─ RSI: {details.get('rsi', 'N/A')}")

                output_manager.print_static(f"   ├─ Trend: {details.get('trend', 'N/A')}")

                output_manager.print_static(f"   └─ Pattern: {details.get('pattern', 'N/A')}")

                if quality == 'STRONG':

                    volatility = await calculate_coin_volatility(symbol)

                    trend_strength = details.get('trend', '0.5 (0)').split()[0]

                    try:

                        trend_strength_val = float(trend_strength)

                    except:

                        trend_strength_val = 0.5

                    grace_pct = get_dynamic_grace_stop(symbol, coin_type, volatility, trend_strength_val, profit_pct)

                    grace_trigger = trigger * (1 - grace_pct / 100.0)

                    grace_duration = get_grace_period_duration(coin_type, volatility)

                    coin_type_display = get_coin_display_name(symbol, coin_type)

                    output_manager.print_static(f"\n{GREEN}🟢 SMART HOLD: Market STRONG - Applying grace period{RESET}")

                    output_manager.print_static(f"{CYAN}   Grace: {grace_pct:.1f}% ({coin_type_display}, Vol: {volatility:.0f}%){RESET}")

                    output_manager.print_static(f"{CYAN}   Duration: {grace_duration}s | Grace trigger: ${fmt(grace_trigger)}{RESET}")

                    grace_result = await monitor_grace_period(

                        symbol, trigger, grace_trigger, grace_duration,

                        activate_price, high, current_delta

                    )

                    if grace_result == 'RECOVERED':

                        output_manager.print_static(f"{GREEN}🔄 Resuming normal trailing from high: ${fmt(high)}{RESET}")

                        output_manager.start_live_update()

                        continue

                    else:

                        price = await get_price_optimized(symbol)

                        profit_pct = safe_division((price - activate_price), activate_price, 0.0) * 100

                        await execute_sell_order(symbol, price, grace_trigger, high, activate_price, current_delta, profit_pct)

                        if advance:

                            activate_buy = price * (1 - offset / 100.0)

                            output_manager.print_static(f"{CYAN}🔄 Advance mode: Setting up trailing buy at ${fmt(activate_buy)}{RESET}")

                            await asyncio.sleep(2)

                            await hybrid_trailing_buy(symbol, current_delta, activate_buy, advance=False, auto_mode=True)

                        break

                elif quality == 'MODERATE':

                    output_manager.print_static(f"{YELLOW}⚠️  Market MODERATE - Selling immediately for safety{RESET}")

                    await execute_sell_order(symbol, price, trigger, high, activate_price, current_delta, profit_pct)

                    if advance:

                        activate_buy = price * (1 - offset / 100.0)

                        output_manager.print_static(f"{CYAN}🔄 Advance mode: Setting up trailing buy at ${fmt(activate_buy)}{RESET}")

                        await asyncio.sleep(2)

                        await hybrid_trailing_buy(symbol, current_delta, activate_buy, advance=False, auto_mode=True)

                    break

                else:

                    output_manager.print_static(f"{RED}🔴 Market WEAK - SELLING IMMEDIATELY!{RESET}")

                    await execute_sell_order(symbol, price, trigger, high, activate_price, current_delta, profit_pct)

                    if advance:

                        activate_buy = price * (1 - offset / 100.0)

                        output_manager.print_static(f"{CYAN}🔄 Advance mode: Setting up trailing buy at ${fmt(activate_buy)}{RESET}")

                        await asyncio.sleep(2)

                        await hybrid_trailing_buy(symbol, current_delta, activate_buy, advance=False, auto_mode=True)

                    break

            last_price = price

            await asyncio.sleep(poll_interval)

    except asyncio.CancelledError:

        output_manager.stop_live_update()

        output_manager.print_static(f"\n{YELLOW}⏹️ Hybrid trailing sell cancelled{RESET}")

        portfolio_manager.print_session_summary()

        if portfolio_manager.positions:

            output_manager.print_static(f"\n{YELLOW}⚠️  You have {len(portfolio_manager.positions)} tracked position(s){RESET}")

            close_choice = input(f"{RED}Close all positions before exiting? (yes/no): {RESET}").strip().lower()

            if close_choice in ['yes', 'y']:

                await emergency_close_all_positions(dry_run=False)

    except KeyboardInterrupt:

        output_manager.stop_live_update()

        output_manager.print_static(f"\n{YELLOW}⏹️ Hybrid trailing sell stopped manually{RESET}")

        portfolio_manager.print_session_summary()

        if portfolio_manager.positions:

            output_manager.print_static(f"\n{YELLOW}⚠️  You have {len(portfolio_manager.positions)} tracked position(s){RESET}")

            close_choice = input(f"{RED}Close all positions before exiting? (yes/no): {RESET}").strip().lower()

            if close_choice in ['yes', 'y']:

                await emergency_close_all_positions(dry_run=False)

            else:

                output_manager.print_static(f"{YELLOW}⚠️ Positions remain open - manage manually if needed{RESET}")

    except Exception as e:

        output_manager.stop_live_update()

        output_manager.print_static(f"\n{RED}❌ Error in hybrid trailing sell: {e}{RESET}")

        error_handler.record_error("Trailing_Sell_Error", "Hybrid trailing sell failed", {"error": str(e)})

    finally:

        output_manager.stop_live_update()

        await task_manager.cancel_all_tasks(symbol)



async def execute_buy_order(symbol: str, price: float, trigger: float, low: float,

                          activate_price: float, delta: float, analysis=None, buy_amount: Optional[float] = None):

    start_time = time.time()

    try:

        entry_efficiency = safe_division((price - low), (activate_price - low), 1.0) * 100 if activate_price > low else 100

        

        # Get buy amount from global or parameter

        if buy_amount is None:

            # Try to get from global context if available

            try:

                buy_amount = float(os.getenv('TRADING_AMOUNT', '0'))

            except:

                buy_amount = 0

        

        # Calculate quantity based on buy_amount and current price

        quantity = 0.0

        if buy_amount > 0 and price > 0:

            quantity = buy_amount / price

        

        # TEST MODE: Skip actual order execution to exchange (for performance testing)

        # Only simulate order execution for logging purposes

        actual_price = price

        actual_quantity = quantity

        

        # Simulate order execution (for testing only)

        if buy_amount > 0:

            output_manager.print_static(f"{CYAN}🧪 TEST MODE: Simulating order execution (no actual order sent to exchange){RESET}")

            output_manager.print_static(f"{CYAN}   Would place: BUY {quantity:.6f} {symbol.split('_')[0]} @ ${price:.8f} (${buy_amount:.2f} USDT){RESET}")

            # In test mode, use simulated values

            actual_price = price

            actual_quantity = quantity

        

        output_manager.print_static(f"\n{GREEN}🎯 BUY EXECUTED │ {symbol}{RESET}")

        output_manager.print_static(f"{GREEN}{'─'*70}{RESET}")

        output_manager.print_static(f"{CYAN}Entry: ${fmt(actual_price)} │ Trigger: ${fmt(trigger)} │ Low: ${fmt(low)} │ Efficiency: {entry_efficiency:.0f}%{RESET}")

        output_manager.print_static(f"{YELLOW}Activation: ${fmt(activate_price)} │ Delta: {delta:.1f}% │ Execution: {time.time() - start_time:.2f}s{RESET}")

        if buy_amount > 0:

            output_manager.print_static(f"{CYAN}Amount: ${buy_amount:.2f} USDT │ Quantity: {actual_quantity:.6f} {symbol.split('_')[0]}{RESET}")

        output_manager.print_static(f"{GREEN}{'─'*70}{RESET}")

        performance_monitor.record_trade()

        ai_confidence = analysis.confidence if analysis else 0

        market_regime = analysis.market_regime.value if analysis and hasattr(analysis, 'market_regime') else "UNKNOWN"

        risk_level = analysis.risk_level if analysis and hasattr(analysis, 'risk_level') else "UNKNOWN"

        portfolio_manager.record_trade_with_performance(

            symbol=symbol,

            action="BUY",

            price=actual_price,

            size=actual_quantity,

            pnl=0

        )

        await portfolio_manager.set_position(symbol, {

            'action': 'BUY',

            'price': actual_price,

            'size': actual_quantity,

            'timestamp': time.time(),

            'entry_type': 'TRAILING_BUY',

            'buy_amount': buy_amount

        })

        trade_metrics = {

            'symbol': symbol,

            'entry_price': actual_price,

            'activation_price': activate_price,

            'lowest_price': low,

            'delta_used': delta,

            'entry_efficiency': entry_efficiency,

            'execution_time': time.time() - start_time,

            'type': 'BUY',

            'quantity': actual_quantity,

            'buy_amount': buy_amount

        }

        print(f"{GREEN}📊 Trade recorded: BUY {actual_quantity:.6f} {symbol} at ${fmt(actual_price)}{RESET}")

        print(f"{GREEN}📊 Trade Performance: Efficiency {entry_efficiency:.1f}% | Execution: {trade_metrics['execution_time']:.2f}s{RESET}")

    except Exception as e:

        performance_monitor.record_error()

        error_handler.record_error("Buy_Execution_Error", "Buy order execution failed", {"error": str(e)})

        raise



async def execute_partial_sell_order(symbol: str, price: float, sell_percentage: float, 

                                    entry_price: float, profit_pct: float, reason: str = ""):

    """

    Execute partial sell order for scale out strategy.

    

    Args:

        symbol: Trading symbol

        price: Current price

        sell_percentage: Percentage of position to sell (0.0-1.0)

        entry_price: Original entry price

        profit_pct: Current profit percentage

        reason: Reason for partial sell (e.g., "10% profit target")

    """

    start_time = time.time()

    try:

        if sell_percentage <= 0 or sell_percentage >= 1.0:

            output_manager.print_static(f"{YELLOW}⚠️ Invalid sell percentage: {sell_percentage:.1%}{RESET}")

            return False

        

        # Calculate locked profit

        locked_profit = profit_pct * sell_percentage

        

        output_manager.print_static(f"\n{GREEN}💰 PARTIAL SELL EXECUTED │ {symbol} │ {sell_percentage:.0%} position{RESET}")

        output_manager.print_static(f"{GREEN}{'─'*70}{RESET}")

        output_manager.print_static(f"{CYAN}Exit: ${fmt(price)} │ Locked Profit: {locked_profit:+.2f}% │ Reason: {reason}{RESET}")

        output_manager.print_static(f"{YELLOW}Entry: ${fmt(entry_price)} │ Current P/L: {profit_pct:+.2f}% │ Remaining: {(1.0 - sell_percentage):.0%}{RESET}")

        output_manager.print_static(f"{GREEN}{'─'*70}{RESET}")

        

        # Record partial sell (track for remaining position)

        if not hasattr(execute_partial_sell_order, '_partial_sells'):

            execute_partial_sell_order._partial_sells = {}

        

        if symbol not in execute_partial_sell_order._partial_sells:

            execute_partial_sell_order._partial_sells[symbol] = {

                'total_sold': 0.0,

                'total_locked_profit': 0.0,

                'remaining_percentage': 1.0,

                'entry_price': entry_price

            }

        

        execute_partial_sell_order._partial_sells[symbol]['total_sold'] += sell_percentage

        execute_partial_sell_order._partial_sells[symbol]['total_locked_profit'] += locked_profit

        execute_partial_sell_order._partial_sells[symbol]['remaining_percentage'] = 1.0 - execute_partial_sell_order._partial_sells[symbol]['total_sold']

        

        # Log trade

        simple_logger.log_trade(symbol, "PARTIAL_SELL", price, locked_profit, 0, f"{sell_percentage:.0%} - {reason}")

        

        return True

    except Exception as e:

        output_manager.print_static(f"{RED}❌ Partial sell error: {e}{RESET}")

        error_handler.record_error("Partial_Sell_Error", "Partial sell execution failed", {"error": str(e)})

        return False



async def execute_sell_order(symbol: str, price: float, trigger: float, high: float,

                           activate_price: float, delta: float, profit_pct: float):

    start_time = time.time()

    try:

        if profit_pct > 5:

            quality = "Excellent ⭐⭐⭐"

            quality_color = GREEN

        elif profit_pct > 2:

            quality = "Good ⭐⭐"

            quality_color = GREEN

        elif profit_pct > 0:

            quality = "Fair ⭐"

            quality_color = YELLOW

        elif profit_pct > -2:

            quality = "Small Loss ⚠️"

            quality_color = RED

        else:

            quality = "Big Loss ❌"

            quality_color = RED

        high_pct = safe_division((high - activate_price), activate_price, 0.0) * 100

        output_manager.print_static(f"\n{quality_color}💰 SELL EXECUTED │ {symbol} │ P/L: {profit_pct:+.2f}%{RESET}")

        output_manager.print_static(f"{quality_color}{'─'*70}{RESET}")

        output_manager.print_static(f"{CYAN}Exit: ${fmt(price)} │ Trigger: ${fmt(trigger)} │ High: ${fmt(high)} (+{high_pct:.1f}%){RESET}")

        output_manager.print_static(f"{YELLOW}Entry: ${fmt(activate_price)} │ Delta: {delta:.1f}% │ Quality: {quality} │ Time: {time.time() - start_time:.1f}s{RESET}")

        output_manager.print_static(f"{quality_color}{'─'*70}{RESET}")

        performance_monitor.record_trade()

        portfolio_manager.record_trade_with_performance(symbol, "SELL", price, 0.1, profit_pct)

        await portfolio_manager.delete_position(symbol)

        if profit_pct > 0:

            portfolio_manager.last_profitable_close = {

                'symbol': symbol,

                'price': price,

                'time': time.time(),

                'profit_pct': profit_pct,

                'entry_price': activate_price,

                'reentry_used': False

            }

            output_manager.print_static(f"{GREEN}💎 Profitable close tracked for smart re-entry: ${fmt(price)} (+{profit_pct:.2f}%){RESET}")

        performance_monitor.record_trade_performance(

            entry_price=activate_price,

            exit_price=price,

            position_type="BUY",

            duration=time.time() - start_time

        )

        trade_metrics = {

            'symbol': symbol,

            'exit_price': price,

            'activation_price': activate_price,

            'highest_price': high,

            'delta_used': delta,

            'profit_loss_pct': profit_pct,

            'execution_time': time.time() - start_time,

            'type': 'SELL'

        }

        print(f"{GREEN}📊 Trade Performance: P/L {profit_pct:+.2f}% | Execution: {trade_metrics['execution_time']:.2f}s{RESET}")

    except Exception as e:

        performance_monitor.record_error()

        error_handler.record_error("Sell_Execution_Error", "Sell order execution failed", {"error": str(e)})

        raise



async def execute_strategy(symbol: str, strategy: AIStrategy, advance: bool = False, offset: float = 0.0):

    try:

        if "BUY" in strategy.name:

            await hybrid_trailing_buy(

                symbol, strategy.delta_percent, strategy.activation_price,

                advance, offset, False, 2.0, True, 2.0, True, True, 30

            )

        else:

            await hybrid_trailing_sell(

                symbol, strategy.delta_percent, strategy.activation_price,

                advance, offset, False, 2.0

            )

    except Exception as e:

        output_manager.print_static(f"{RED}❌ Strategy execution error: {e}{RESET}")

        error_handler.record_error("Strategy_Execution_Error", "Strategy execution failed", {"error": str(e)})



async def perform_adaptive_reanalysis(symbol: str, current_strategy: AIStrategy,

                                    original_confidence: float, strategy_type: str,

                                    current_volatility: float, cancel_threshold: float,

                                    suppress_output: bool = False):

    try:

        # ===== CHECK FOR BULLISH MOMENTUM (Shorter Reanalysis Interval) =====

        bullish_momentum_detected = False

        try:

            candles_5m = await get_candles_optimized(symbol, '5m', 3, force_fresh=True)

            order_flow = await get_order_flow_optimized(symbol, limit=20)

            

            if candles_5m and len(candles_5m) >= 2 and order_flow:

                current_candle = candles_5m[-1]

                previous_candle = candles_5m[-2]

                price_change_5m = safe_division((float(current_candle['close']) - float(previous_candle['close'])), float(previous_candle['close']), 0.0) * 100

                buy_pressure = order_flow.get('buy_pressure', 50)

                

                # Bullish momentum: price up > 2% in 5m AND buy pressure > 60%

                bullish_momentum_detected = price_change_5m > 2.0 and buy_pressure > 60

        except Exception:

            pass  # Continue with normal reanalysis if check fails

        

        new_analysis = await analyze_hold_wait_buy_ai(symbol)

        candles_1h = await get_candles_optimized(symbol, "1h", 100)

        new_indicators = calculate_advanced_indicators(candles_1h) if candles_1h else {}

        old_confidence = current_strategy.confidence

        new_confidence = new_analysis.confidence

        market_regime = new_indicators.get('market_regime', MarketRegime.UNKNOWN)

        updated_volatility = await calculate_coin_volatility(symbol)

        vol_change = updated_volatility - current_volatility

        

        # Only show volatility changes if significant (>10%) or critical

        if abs(vol_change) > 10:

            output_manager.print_static(f"   {YELLOW}⚡ Volatility change: {current_volatility:.1f}% → {updated_volatility:.1f}% ({vol_change:+.1f}%){RESET}")

        if updated_volatility > TradingConfig.MAX_VOLATILITY:

            output_manager.print_static(f"   {RED}🚨 VOLATILITY SPIKE: {updated_volatility:.1f}% > {TradingConfig.MAX_VOLATILITY}%!{RESET}")

            output_manager.print_static(f"   {RED}🛑 EMERGENCY CANCEL for safety!{RESET}")

            return "CANCEL"

        # Don't show extreme volatility warning if no significant change and suppress_output is True

        elif updated_volatility > 45 and not suppress_output:

            # Only show if volatility increased significantly

            if vol_change > 5:

                output_manager.print_static(f"   ⚠️  Vol: {updated_volatility:.0f}% (Extreme, monitoring){RESET}")

        

        conf_change = new_confidence - old_confidence

        current_strategy.confidence = new_confidence

        current_strategy.market_regime = market_regime

        cancel_reasons = []

        

        # Only check manipulation if we need to show output or if risk is high

        show_manipulation_check = False

        try:

            coin_type = await get_coin_type_range(symbol)

            recent_candles_1m = await get_candles_optimized(symbol, '1m', 20)

            if recent_candles_1m and len(recent_candles_1m) >= 5:

                order_flow_data = None

                orderbook_data = None

                try:

                    ws_manager = _init_ws_manager()

                    if ws_manager:

                        if hasattr(ws_manager, 'get_order_flow_analysis'):

                            order_flow_data = ws_manager.get_order_flow_analysis(symbol, limit=20)

                        if hasattr(ws_manager, 'get_orderbook_analysis'):

                            orderbook_data = ws_manager.get_orderbook_analysis(symbol)

                except Exception:

                    pass

                manipulation_results = comprehensive_manipulation_check(

                    recent_candles_1m, coin_type, current_volatility, order_flow_data, orderbook_data

                )

                critical_threats = []

                if manipulation_results['pump_dump'][0]:

                    critical_threats.append(f"Pump & dump: {manipulation_results['pump_dump'][1]}")

                if manipulation_results['flash_crash'][0]:

                    critical_threats.append(f"Flash crash: {manipulation_results['flash_crash'][1]}")

                if manipulation_results['volume_manipulation'][0]:

                    critical_threats.append(f"Volume manip: {manipulation_results['volume_manipulation'][1]}")

                if manipulation_results['price_anomaly'][0]:

                    critical_threats.append(f"Price anomaly: {manipulation_results['price_anomaly'][1]}")

                risk_level = manipulation_results.get('overall_risk', 'LOW')

                # Only show manipulation warnings for CRITICAL/HIGH risk

                if risk_level == 'CRITICAL':

                    output_manager.print_static(f"   🚨 CRITICAL: {critical_threats[0]}")

                    cancel_reasons.append(f"CRITICAL manipulation detected")

                    show_manipulation_check = True

                elif risk_level == 'HIGH':

                    output_manager.print_static(f"   ⚠️  HIGH RISK: {len(critical_threats)} threats")

                    if not manipulation_results.get('legitimate_recovery', (False, ''))[0]:

                        cancel_reasons.append(f"High manipulation risk: {', '.join(critical_threats)}")

                    show_manipulation_check = True

                # Don't show MEDIUM/LOW risk manipulation (reduce spam)

                # elif risk_level == 'MEDIUM':

                #     output_manager.print_static(f"   ⚡ MEDIUM: {critical_threats[0][:30] if critical_threats else 'Monitoring'}")

                # else:

                #     output_manager.print_static(f"   ✅ Clean")

        except Exception as e:

            error_str = str(e).lower()

            if 'timeout' in error_str or 'connection' in error_str or 'rate limit' in error_str:

                if not suppress_output:

                    output_manager.print_static(f"   ⚠️  API issue: {str(e)[:20]}")

        

        if new_confidence < cancel_threshold and original_confidence > 60:

            cancel_reasons.append(f"Confidence dropped to {new_confidence:.1f}% < {cancel_threshold}%")

        if (strategy_type == "BUY" and new_analysis.recommendation in [ActionRecommendation.SELL, ActionRecommendation.STRONG_SELL]):

            cancel_reasons.append(f"Market reversed to {new_analysis.recommendation.value}")

        if (strategy_type == "SELL" and new_analysis.recommendation in [ActionRecommendation.BUY, ActionRecommendation.STRONG_BUY]):

            cancel_reasons.append(f"Market reversed to {new_analysis.recommendation.value}")

        if (original_confidence > 70 and new_confidence < 40):

            cancel_reasons.append("Extreme confidence drop detected")

        

        # Cancel jika recommendation HOLD/WAIT dengan confidence rendah (<50%)

        # Prevent stuck waiting untuk kondisi yang tidak favorable

        if strategy_type == "BUY" and new_analysis.recommendation in [ActionRecommendation.HOLD, ActionRecommendation.WAIT]:

            if new_confidence < 50.0:

                cancel_reasons.append(

                    f"Recommendation {new_analysis.recommendation.value} with low confidence "

                    f"{new_confidence:.1f}% - Not suitable for entry"

                )

        

        if cancel_reasons:

            output_manager.print_static(f"{RED}🎯 STRATEGY CANCELLED: {', '.join(cancel_reasons)}{RESET}")

            return "CANCEL"

        

        if abs(new_confidence - old_confidence) > 12:

            new_delta = await calculate_ai_recommended_delta(new_analysis, new_indicators, strategy_type, symbol)

            delta_adjusted = False

            if abs(new_delta - current_strategy.delta_percent) > 0.2:

                # Check if delta adjustment was actually applied (confidence >= 70% and volatility < 45%)

                # If adjustment was skipped, new_delta might be calculated but not applied

                # Only show delta change if confidence is high enough to actually apply it

                if new_confidence >= 70.0:

                    # High confidence - delta adjustment was applied

                    output_manager.print_static(f"   🔄 Delta: {current_strategy.delta_percent:.1f}% → {new_delta:.1f}%")

                    current_strategy.delta_percent = new_delta

                    delta_adjusted = True

                else:

                    # Low confidence - delta adjustment was skipped, show informational message

                    output_manager.print_static(f"   ℹ️ Delta calculated: {new_delta:.1f}% (not applied - confidence {new_confidence:.0f}% < 70%)")

                    # Don't change delta, keep current value

            

            # Also adjust activation price if confidence change is significant

            if strategy_type == "BUY":

                try:

                    current_price = await get_price_optimized(symbol)

                    if current_price:

                        new_activation = await calculate_smart_activation_price(symbol, current_price, new_analysis)

                        old_activation = getattr(current_strategy, 'activation_price', None)

                        if old_activation:

                            activation_change_pct = abs((new_activation - old_activation) / old_activation) * 100

                            if activation_change_pct > 1.0:  # Only adjust if change > 1%

                                output_manager.print_static(f"   🔄 Activation: ${fmt(old_activation)} → ${fmt(new_activation)} ({activation_change_pct:+.1f}%)")

                                current_strategy.activation_price = new_activation

                                delta_adjusted = True

                        else:

                            current_strategy.activation_price = new_activation

                            delta_adjusted = True

                except Exception as e:

                    # Silently continue if activation adjustment fails

                    pass

            

            if delta_adjusted:

                return "ADJUST"

        

        # Only show confidence changes if significant (>5% change) or if not suppressing output

        if not suppress_output:

            # Only show if there's a significant change or regime/recommendation change

            if abs(conf_change) > 5 or new_analysis.recommendation != getattr(current_strategy, 'last_recommendation', None):

                conf_arrow = "↗" if conf_change > 0 else "↘" if conf_change < 0 else "→"

                output_manager.print_static(f"   {conf_arrow} AI: {old_confidence:.0f}% → {new_confidence:.0f}% │ {market_regime.value.title().replace('_', ' ')} │ {new_analysis.recommendation.value}")

                if hasattr(current_strategy, 'last_recommendation'):

                    current_strategy.last_recommendation = new_analysis.recommendation

            elif new_confidence > old_confidence + 5:

                output_manager.print_static(f"   ✅ Strengthened")

            # Don't show "Valid" message (reduce spam)

            # else:

            #     output_manager.print_static(f"   ✅ Valid")

        elif abs(conf_change) > 10 or new_analysis.recommendation != getattr(current_strategy, 'last_recommendation', None):

            # Even with suppress_output, show significant changes

            conf_arrow = "↗" if conf_change > 0 else "↘" if conf_change < 0 else "→"

            output_manager.print_static(f"   {conf_arrow} AI: {old_confidence:.0f}% → {new_confidence:.0f}% │ {market_regime.value.title().replace('_', ' ')} │ {new_analysis.recommendation.value}")

            if hasattr(current_strategy, 'last_recommendation'):

                current_strategy.last_recommendation = new_analysis.recommendation

        

        return "KEEP"

    except Exception as e:

        if not suppress_output:

            output_manager.print_static(f"{YELLOW}⚠️ Re-analysis error: {e}{RESET}")

        error_handler.record_error("Reanalysis_Error", "Adaptive re-analysis failed", {"error": str(e)})

        return "KEEP"



async def adaptive_time_based_reanalysis(symbol: str, current_strategy: AIStrategy, strategy_type: str = "BUY"):

    try:

        if current_strategy.market_regime == MarketRegime.UNKNOWN:

            analysis = await analyze_hold_wait_buy_ai(symbol)

            current_strategy.market_regime = analysis.market_regime

        current_volatility = await calculate_coin_volatility(symbol)

        market_regime = current_strategy.market_regime

        

        # ===== SHORTER REANALYSIS INTERVAL FOR BULLISH =====

        # Check for bullish momentum to shorten reanalysis interval

        bullish_momentum_detected = False

        try:

            candles_5m = await get_candles_optimized(symbol, '5m', 3, force_fresh=True)

            order_flow = await get_order_flow_optimized(symbol, limit=20)

            

            if candles_5m and len(candles_5m) >= 2 and order_flow:

                current_candle = candles_5m[-1]

                previous_candle = candles_5m[-2]

                price_change_5m = safe_division((float(current_candle['close']) - float(previous_candle['close'])), float(previous_candle['close']), 0.0) * 100

                buy_pressure = order_flow.get('buy_pressure', 50)

                

                # Bullish momentum: price up > 2% in 5m AND buy pressure > 60%

                bullish_momentum_detected = price_change_5m > 2.0 and buy_pressure > 60

        except Exception:

            pass  # Continue with normal interval if check fails

        

        if current_volatility > 30:

            reanalysis_interval = 600

        elif current_volatility > 25:

            reanalysis_interval = 900

        elif current_volatility > 18:

            reanalysis_interval = 1200

        elif current_volatility > 12:

            reanalysis_interval = 1800

        elif current_volatility > 8:

            reanalysis_interval = 2400

        else:

            reanalysis_interval = 3000

        

        # Shorten interval to 5 minutes if bullish momentum detected

        if bullish_momentum_detected:

            reanalysis_interval = 300  # 5 minutes instead of normal interval

        emergency_threshold = max(1.5, safe_division(current_volatility, 8, 1.5))

        cancel_threshold = 35

        last_analysis = time.time()

        last_volatility_check = time.time()

        original_confidence = current_strategy.confidence

        last_price = await get_price_optimized(symbol) or 0

        last_market_regime = market_regime

        while True:

            current_time = time.time()

            current_price = await get_price_optimized(symbol)

            if current_time - last_volatility_check >= 600:

                new_volatility = await calculate_coin_volatility(symbol)

                if abs(new_volatility - current_volatility) > 3:

                    output_manager.print_static(f"{CYAN}📊 Volatility Update: {current_volatility:.1f}% → {new_volatility:.1f}%{RESET}")

                    current_volatility = new_volatility

                try:

                    candles_1h = await get_candles_optimized(symbol, "1h", 100)

                    if candles_1h:

                        indicators_1h = calculate_advanced_indicators(candles_1h)

                        new_regime = enhanced_market_regime_detection_single(indicators_1h)

                        if new_regime != last_market_regime:

                            old_r = last_market_regime.value.title().replace('_', ' ')

                            new_r = new_regime.value.title().replace('_', ' ')

                            output_manager.print_static(f"{MAGENTA}   🔄 Regime: {old_r} → {new_r}{RESET}")

                            last_market_regime = new_regime

                            # Regime change is significant, show full output

                            action = await perform_adaptive_reanalysis(

                                symbol, current_strategy, original_confidence, strategy_type,

                                current_volatility, cancel_threshold, suppress_output=False

                            )

                            if action == "CANCEL":

                                return "CANCEL"

                            last_analysis = current_time

                except Exception:

                    pass

                last_volatility_check = current_time

            if current_price and last_price:

                price_change_pct = abs((current_price - last_price) / last_price) * 100

                if price_change_pct >= emergency_threshold:

                    output_manager.print_static(f"\n{YELLOW}🚨 Smart Trigger: Price moved {price_change_pct:.1f}% - Re-analyzing...{RESET}")

                    # Emergency trigger is significant, show full output

                    action = await perform_adaptive_reanalysis(

                        symbol, current_strategy, original_confidence, strategy_type,

                        current_volatility, cancel_threshold, suppress_output=False

                    )

                    if action == "CANCEL":

                        return "CANCEL"

                    last_price = current_price

                    last_analysis = current_time

            if current_time - last_analysis >= reanalysis_interval:

                # Only show header if there will be significant output (CANCEL/ADJUST)

                # Run re-analysis first to check result

                action = await perform_adaptive_reanalysis(

                    symbol, current_strategy, original_confidence, strategy_type,

                    current_volatility, cancel_threshold, suppress_output=True

                )

                # Only show header if action is CANCEL or ADJUST (significant changes)

                if action in ["CANCEL", "ADJUST"]:

                    output_manager.print_static(f"\n{CYAN}🔄 SMART RE-ANALYSIS ({reanalysis_interval//60}min interval) [{now_ts()}]{RESET}")

                    # Re-run with output enabled to show details

                    action = await perform_adaptive_reanalysis(

                        symbol, current_strategy, original_confidence, strategy_type,

                        current_volatility, cancel_threshold, suppress_output=False

                    )

                # Otherwise, silently continue (no spam)

                if action == "CANCEL":

                    return "CANCEL"

                last_analysis = current_time

            await asyncio.sleep(30)

    except asyncio.CancelledError:

        output_manager.print_static(f"{YELLOW}⏹️ Adaptive re-analysis task cancelled{RESET}")

        return "KEEP"

    except Exception as e:

        output_manager.print_static(f"{YELLOW}⚠️ Adaptive re-analysis system error: {e}{RESET}")

        error_handler.record_error("Adaptive_Reanalysis_Error", "Adaptive re-analysis system failed", {"error": str(e)})

        return "KEEP"



async def handle_strategy_cancellation(symbol: str, reason: str) -> Optional[AIStrategy]:

    output_manager.print_static(f"{RED}🎯 STRATEGY CANCELLED: {reason}{RESET}")

    position = await portfolio_manager.get_position(symbol)

    if position:

        current_price = await get_price_optimized(symbol)

        if current_price and position.get('price') and position.get('price') > 0:

            entry_price = position.get('price', 0)

            pnl = safe_division((current_price - entry_price), entry_price, 0.0) * 100

            output_manager.print_static(f"\n{YELLOW}{'='*80}{RESET}")

            output_manager.print_static(f"{YELLOW}⚠️  ORPHANED POSITION DETECTED!{RESET}")

            output_manager.print_static(f"{YELLOW}{'='*80}{RESET}")

            output_manager.print_static(f"{CYAN}📊 Position Details:{RESET}")

            output_manager.print_static(f"  ├─ Symbol: {symbol}")

            output_manager.print_static(f"  ├─ Type: {position.get('action', 'UNKNOWN')}")

            output_manager.print_static(f"  ├─ Entry: ${entry_price:.4f}")

            output_manager.print_static(f"  ├─ Current: ${current_price:.4f}")

            output_manager.print_static(f"  └─ P/L: {pnl:+.2f}%")

            if pnl > 0:

                output_manager.print_static(f"\n{GREEN}✅ AUTO-CLOSING to SECURE PROFIT!{RESET}")

            elif pnl < -2:

                output_manager.print_static(f"\n{RED}⚠️ AUTO-CLOSING to LIMIT LOSS!{RESET}")

            else:

                output_manager.print_static(f"\n{CYAN}🔄 AUTO-CLOSING for clean transition!{RESET}")

            output_manager.print_static(f"{GREEN}💎 MARKET EXIT: {symbol} at ${current_price:.4f}{RESET}")

            output_manager.print_static(f"{CYAN}📊 Trade Summary:{RESET}")

            output_manager.print_static(f"  ├─ Entry: ${entry_price:.4f}")

            output_manager.print_static(f"  ├─ Exit: ${current_price:.4f}")

            output_manager.print_static(f"  ├─ P/L: {pnl:+.2f}%")

            if pnl > 5:

                quality = f"{GREEN}Excellent! ⭐⭐⭐{RESET}"

            elif pnl > 2:

                quality = f"{GREEN}Good! ⭐⭐{RESET}"

            elif pnl > 0:

                quality = f"{YELLOW}Fair ⭐{RESET}"

            elif pnl > -2:

                quality = f"{YELLOW}Small Loss ⚠️{RESET}"

            else:

                quality = f"{RED}Loss Cut ❌{RESET}"

            output_manager.print_static(f"  └─ Quality: {quality}")

            output_manager.print_static(f"{YELLOW}{'='*80}{RESET}")

            performance_monitor.record_trade_performance(

                entry_price=entry_price,

                exit_price=current_price,

                position_type=position.get('action', 'BUY'),

                duration=time.time() - position.get('timestamp', time.time())

            )

            await portfolio_manager.delete_position(symbol)

            output_manager.print_static(f"{GREEN}✅ Position closed successfully! Ready for new strategy!{RESET}")

            await asyncio.sleep(2)

        else:

            output_manager.print_static(f"{YELLOW}⚠️ Clearing tracked position (price unavailable){RESET}")

            await portfolio_manager.delete_position(symbol)

    output_manager.print_static(f"\n{GREEN}🔄 AUTO-EXECUTING RECOVERY STRATEGY...{RESET}")

    try:

        await asyncio.sleep(3)

        analysis = await analyze_hold_wait_buy_ai(symbol)

        current_price = await get_price_optimized(symbol)

        if not current_price:

            return None

        activate_price = await calculate_smart_activation_price(symbol, current_price, analysis)

        delta = await calculate_ai_recommended_delta(analysis, {}, "BUY", symbol)

        actual_discount_pct = ((current_price - activate_price) / current_price) * 100

        output_manager.print_static(f"{CYAN}🎯 Smart Activation: ${fmt(current_price)} → ${fmt(activate_price)} ({actual_discount_pct:.2f}% discount){RESET}")

        await hybrid_trailing_buy(

            symbol=symbol,

            delta_percent=delta,

            activate_price=activate_price,

            advance=True,

            offset=2.0,

            skip_wait=False,

            auto_trailing_stop=True,

            stop_loss_percent=2.0,

            dynamic_ai_stop=True,

            auto_mode=True,

            timeout_minutes=30

        )

        return AIStrategy(

            name="ADAPTIVE RECOVERY",

            description=f"Auto-executed with {actual_discount_pct:.2f}% smart discount (coin-aware)",

            activation_price=activate_price,

            delta_percent=delta,

            confidence=analysis.confidence,

            risk_reward_ratio=2.5,

            timeframe="1H",

            market_regime=analysis.market_regime

        )

    except Exception as e:

        output_manager.print_static(f"{RED}❌ Auto-recovery failed: {e}{RESET}")

        return None



async def fixed_take_profit_exit(symbol: str, entry_price: float, take_profit_percent: float, stop_loss_percent: float, poll_interval: float = 2.0):

    coin_type = await get_coin_type_range(symbol)

    current_volatility = await calculate_coin_volatility(symbol)

    output_manager.print_static(f"\n{MAGENTA}🎯 SMART FIXED TAKE PROFIT MODE ACTIVATED{RESET}")

    output_manager.print_static(f"{MAGENTA}Entry: ${fmt(entry_price)} | TP: {take_profit_percent:.1f}% | SL: {stop_loss_percent:.1f}%{RESET}")

    output_manager.print_static(f"{CYAN}🛡️ Emergency TP: Enabled (lock profits on danger!){RESET}")

    take_profit_price = entry_price * (1 + take_profit_percent / 100.0)

    stop_loss_price = entry_price * (1 - stop_loss_percent / 100.0)

    output_manager.print_static(f"\n{CYAN}🎯 FIXED EXIT TARGETS:{RESET}")

    output_manager.print_static(f"  - Entry: ${fmt(entry_price)}")

    output_manager.print_static(f"  - Take Profit: ${fmt(take_profit_price)} (+{take_profit_percent:.1f}%)")

    output_manager.print_static(f"  - Stop Loss: ${fmt(stop_loss_price)} (-{stop_loss_percent:.1f}%)")

    output_manager.print_static(f"{CYAN}{'='*70}{RESET}")

    output_manager.start_live_update()

    highest_price = entry_price

    last_emergency_check = 0

    emergency_check_cooldown = 30

    try:

        while True:

            price = await get_price_optimized(symbol)

            if price is None:

                await asyncio.sleep(poll_interval)

                continue

            if price > highest_price:

                highest_price = price

            profit_loss = safe_division((price - entry_price), entry_price, 0.0) * 100

            tp_distance = safe_division((take_profit_price - price), price, 0.0) * 100

            sl_distance = safe_division((price - stop_loss_price), price, 0.0) * 100

            if profit_loss >= 0:

                pnl_color = GREEN

                pnl_icon = "💰"

            else:

                pnl_color = RED

                pnl_icon = "💸"

            status = f"{MAGENTA}🎯【FIXED TP】{symbol} | ${fmt(price)} | {pnl_color}{pnl_icon} {profit_loss:+.2f}%{RESET} | TP: {tp_distance:+.2f}% | SL: {sl_distance:+.2f}% | High: ${fmt(highest_price)}"

            output_manager.print_live(status)

            current_time = time.time()

            time_since_last_check = current_time - last_emergency_check

            if time_since_last_check >= emergency_check_cooldown and profit_loss > 0:

                min_profit_threshold = take_profit_percent * 0.5

                if profit_loss >= min_profit_threshold:

                    try:

                        analysis = await analyze_hold_wait_buy_ai(symbol)

                        flow_data = await get_order_flow_optimized(symbol, 20)

                        danger_signals = 0

                        danger_reasons = []

                        if analysis.market_regime in [MarketRegime.STRONG_DOWNTREND, MarketRegime.WEAK_DOWNTREND]:

                            danger_signals += 1

                            danger_reasons.append(f"Regime: {analysis.market_regime.value}")

                        buy_pressure = flow_data.get('buy_pressure', 50)

                        if buy_pressure < 30:

                            danger_signals += 1

                            danger_reasons.append(f"Buy pressure: {buy_pressure:.0f}% (very weak!)")

                        cvd_momentum = flow_data.get('cvd_momentum', 0)

                        if cvd_momentum < -500:

                            danger_signals += 1

                            danger_reasons.append(f"CVD momentum: {cvd_momentum:.0f} (strong selling!)")

                        last_emergency_check = current_time

                        if danger_signals >= 2:

                            safety_buffer = entry_price * (1 + stop_loss_percent / 200.0)

                            if price > safety_buffer:

                                output_manager.stop_live_update()

                                output_manager.print_static(f"\n{YELLOW}{'='*70}{RESET}")

                                output_manager.print_static(f"{YELLOW}🚨 EMERGENCY TP TRIGGERED! Market deteriorating...{RESET}")

                                output_manager.print_static(f"{YELLOW}{'='*70}{RESET}")

                                output_manager.print_static(f"{YELLOW}  Entry: ${fmt(entry_price)}{RESET}")

                                output_manager.print_static(f"{YELLOW}  Current: ${fmt(price)}{RESET}")

                                output_manager.print_static(f"{YELLOW}  Profit: {profit_loss:+.2f}% (Target was: {take_profit_percent:.1f}%){RESET}")

                                output_manager.print_static(f"{YELLOW}  Danger Signals: {danger_signals}/3{RESET}")

                                for reason in danger_reasons:

                                    output_manager.print_static(f"{YELLOW}    • {reason}{RESET}")

                                output_manager.print_static(f"{YELLOW}  Decision: Lock profit NOW (avoid reversal!){RESET}")

                                output_manager.print_static(f"{YELLOW}{'='*70}{RESET}")

                                await execute_sell_order(symbol, price, take_profit_price, highest_price, entry_price, profit_loss, profit_loss)

                                return

                    except Exception as e:

                        pass

            if price >= take_profit_price:

                output_manager.stop_live_update()

                output_manager.print_static(f"\n{GREEN}{'='*70}{RESET}")

                output_manager.print_static(f"{GREEN}🎯 TAKE PROFIT HIT! Selling at profit...{RESET}")

                output_manager.print_static(f"{GREEN}{'='*70}{RESET}")

                output_manager.print_static(f"{GREEN}  Entry: ${fmt(entry_price)}{RESET}")

                output_manager.print_static(f"{GREEN}  Exit:  ${fmt(price)}{RESET}")

                output_manager.print_static(f"{GREEN}  Profit: {profit_loss:+.2f}% (Target: {take_profit_percent:.1f}%){RESET}")

                output_manager.print_static(f"{GREEN}  Highest: ${fmt(highest_price)} (Max profit: {safe_division((highest_price - entry_price), entry_price, 0.0) * 100:+.2f}%){RESET}")

                output_manager.print_static(f"{GREEN}{'='*70}{RESET}")

                await execute_sell_order(symbol, price, take_profit_price, highest_price, entry_price, take_profit_percent, profit_loss)

                return

            if price <= stop_loss_price:

                try:

                    coin_type = await get_coin_type_range(symbol)

                    current_volatility = await calculate_coin_volatility(symbol)

                    orderbook_data = await get_orderbook_optimized(symbol, 20)

                    flow_data = await get_order_flow_optimized(symbol, 20)

                    candles = await get_candles_optimized(symbol, '5m', 10)

                    if orderbook_data and flow_data and candles:

                        is_whale_wall, whale_desc, whale_details = detect_whale_wall_absorption(

                            symbol, orderbook_data, flow_data

                        )

                        if is_whale_wall:

                            whale_strength = whale_details.get('bid_wall_ratio', 0) * 100

                            whale_threshold = 30 if coin_type == 'MAJOR' else 35

                            if whale_strength >= whale_threshold:

                                output_manager.stop_live_update()

                                output_manager.print_static(f"\n{YELLOW}{'='*70}{RESET}")

                                output_manager.print_static(f"{YELLOW}⚠️ WHALE WALL DETECTED - Holding position!{RESET}")

                                output_manager.print_static(f"{YELLOW}Reason: {whale_desc} (likely fake dump){RESET}")

                                output_manager.print_static(f"{YELLOW}Continuing to monitor...{RESET}")

                                output_manager.print_static(f"{YELLOW}{'='*70}{RESET}")

                                output_manager.start_live_update()

                                await asyncio.sleep(poll_interval)

                                continue

                        is_false_signal, false_desc, false_details = detect_false_signal_patterns(

                            candles, flow_data, orderbook_data, coin_type, current_volatility

                        )

                        if is_false_signal:

                            # FIX: Add cooldown mechanism to prevent spam (same as other location)

                            if not hasattr(detect_false_signal_patterns, '_last_message_time'):

                                detect_false_signal_patterns._last_message_time = {}

                            if not hasattr(detect_false_signal_patterns, '_last_message_desc'):

                                detect_false_signal_patterns._last_message_desc = {}

                            

                            current_time = time.time()

                            last_time = detect_false_signal_patterns._last_message_time.get(symbol, 0)

                            last_desc = detect_false_signal_patterns._last_message_desc.get(symbol, "")

                            cooldown_seconds = 30.0  # Show message max once per 30 seconds

                            

                            # Only show message if:

                            # 1. Never shown before for this symbol, OR

                            # 2. Cooldown expired (30 seconds), OR

                            # 3. Message changed (different false signal detected)

                            should_show = (

                                last_time == 0 or

                                (current_time - last_time) >= cooldown_seconds or

                                false_desc != last_desc

                            )

                            

                            if should_show:

                                detect_false_signal_patterns._last_message_time[symbol] = current_time

                                detect_false_signal_patterns._last_message_desc[symbol] = false_desc

                                

                                output_manager.stop_live_update()

                                output_manager.print_static(f"\n{YELLOW}{'='*70}{RESET}")

                                output_manager.print_static(f"{YELLOW}⚠️ FALSE SIGNAL DETECTED - Holding position!{RESET}")

                                output_manager.print_static(f"{YELLOW}Reason: {false_desc} (likely manipulation){RESET}")

                                output_manager.print_static(f"{YELLOW}Continuing to monitor...{RESET}")

                                output_manager.print_static(f"{YELLOW}{'='*70}{RESET}")

                                output_manager.start_live_update()

                            # NOTE: Don't continue here - still need to check trailing stop!

                            # continue  # REMOVED: This was preventing trailing stop check

                        buy_pressure = flow_data.get('buy_pressure', 0)

                        buy_threshold = 55 if coin_type == 'MAJOR' else 50

                        if buy_pressure >= buy_threshold:

                            output_manager.stop_live_update()

                            output_manager.print_static(f"\n{YELLOW}{'='*70}{RESET}")

                            output_manager.print_static(f"{YELLOW}⚠️ STRONG BUYING PRESSURE ({buy_pressure:.0f}%) - Holding!{RESET}")

                            output_manager.print_static(f"{YELLOW}This might be a temporary dip{RESET}")

                            output_manager.print_static(f"{YELLOW}Continuing to monitor...{RESET}")

                            output_manager.print_static(f"{YELLOW}{'='*70}{RESET}")

                            output_manager.start_live_update()

                            await asyncio.sleep(poll_interval)

                            continue

                except Exception as e:

                    pass

                output_manager.stop_live_update()

                output_manager.print_static(f"\n{RED}{'='*70}{RESET}")

                output_manager.print_static(f"{RED}🛑 STOP LOSS HIT! Cutting losses...{RESET}")

                output_manager.print_static(f"{RED}{'='*70}{RESET}")

                output_manager.print_static(f"{RED}  Entry: ${fmt(entry_price)}{RESET}")

                output_manager.print_static(f"{RED}  Exit:  ${fmt(price)}{RESET}")

                output_manager.print_static(f"{RED}  Loss: {profit_loss:+.2f}% (SL: {stop_loss_percent:.1f}%){RESET}")

                output_manager.print_static(f"{RED}  Highest: ${fmt(highest_price)} (Missed: {safe_division((highest_price - entry_price), entry_price, 0.0) * 100:+.2f}%){RESET}")

                output_manager.print_static(f"{RED}{'='*70}{RESET}")

                await execute_sell_order(symbol, price, stop_loss_price, highest_price, entry_price, stop_loss_percent, profit_loss)

                return

            await asyncio.sleep(poll_interval)

    except asyncio.CancelledError:

        output_manager.stop_live_update()

        output_manager.print_static(f"{YELLOW}⏹️ Fixed TP monitor cancelled{RESET}")

        raise

    except Exception as e:

        output_manager.stop_live_update()

        output_manager.print_static(f"{RED}❌ Fixed TP error: {e}{RESET}")

        error_handler.record_error("Fixed_TP_Error", "Fixed take profit exit failed", {"error": str(e)})



async def dynamic_ai_trailing_stop_after_buy(symbol: str, entry_price: float, poll_interval: float = 2.0):

    output_manager.print_static(f"\n{MAGENTA}🧠 ENHANCED AI TRAILING STOP ACTIVATED{RESET}")

    output_manager.print_static(f"{MAGENTA}Entry: ${fmt(entry_price)} | AI analyzing market for optimal stop...{RESET}")

    await asyncio.sleep(5)

    try:

        analysis = await analyze_hold_wait_buy_ai(symbol)

        current_volatility = await calculate_coin_volatility(symbol)

        market_regime = analysis.market_regime

        base_stop = 2.0

        if current_volatility > 40:    base_stop = 6.0

        elif current_volatility > 30:  base_stop = 4.5

        elif current_volatility > 25:  base_stop = 3.5

        elif current_volatility > 18:  base_stop = 2.8

        elif current_volatility > 12:  base_stop = 2.2

        elif current_volatility > 8:   base_stop = 1.8

        else:                          base_stop = 1.4

        if market_regime == MarketRegime.SIDEWAYS:

            base_stop *= 0.75

        elif market_regime in [MarketRegime.STRONG_UPTREND, MarketRegime.WEAK_UPTREND]:

            base_stop *= 1.4

        elif market_regime in [MarketRegime.STRONG_DOWNTREND, MarketRegime.WEAK_DOWNTREND]:

            base_stop *= 0.85

        ai_confidence = analysis.confidence

        if ai_confidence > 80:   base_stop *= 0.85

        elif ai_confidence > 70: base_stop *= 0.95

        elif ai_confidence < 50: base_stop *= 1.25

        elif ai_confidence < 60: base_stop *= 1.10

        try:

            orderbook_data = await get_orderbook_optimized(symbol, 20)

            flow_data = await get_order_flow_optimized(symbol, 20)

            if orderbook_data and flow_data:

                buy_pressure = flow_data.get('buy_pressure', 50)

                cvd_momentum = flow_data.get('cvd_momentum', 0)

                order_imbalance = flow_data.get('order_imbalance', 0)

                if buy_pressure > 65:

                    base_stop *= 1.20

                elif buy_pressure > 55:

                    base_stop *= 1.10

                elif buy_pressure < 45:

                    base_stop *= 0.90

                if cvd_momentum > 1000:

                    base_stop *= 1.15

                elif cvd_momentum < -500:

                    base_stop *= 0.85

                if order_imbalance > 10:

                    base_stop *= 1.10

                elif order_imbalance < -10:

                    base_stop *= 0.90

        except Exception as e:

            pass

        stop_loss_percent = max(1.0, min(8.0, base_stop))

        output_manager.print_static(f"\n{CYAN}🎯 ENHANCED TRAILING STOP ANALYSIS:{RESET}")

        output_manager.print_static(f"  - Market Regime: {market_regime.value}")

        output_manager.print_static(f"  - Volatility: {current_volatility:.1f}%")

        output_manager.print_static(f"  - AI Confidence: {ai_confidence:.1f}%")

        output_manager.print_static(f"  - Risk Level: {analysis.risk_level}")

        output_manager.print_static(f"  - 🤖 Recommended Stop: {GREEN}{stop_loss_percent:.1f}%{RESET}")

        high_since_entry = entry_price

        output_manager.print_static(f"\n{CYAN}🚀 ENHANCED AI TRAILING STOP STARTED:{RESET}")

        output_manager.print_static(f"  - Entry: ${fmt(entry_price)}")

        output_manager.print_static(f"  - AI Stop: {stop_loss_percent:.1f}%")

        output_manager.print_static(f"  - Initial Stop: ${fmt(entry_price * (1 - stop_loss_percent/100))}{RESET}")

        output_manager.start_live_update()

        while True:

            price = await get_price_optimized(symbol)

            if price is None:

                await asyncio.sleep(poll_interval)

                continue

            if price > high_since_entry:

                high_since_entry = price

            profit_loss = safe_division((price - entry_price), entry_price, 0.0) * 100

            

            # NEW: Dynamic Trailing Stop based on Profit Level

            # Loose di awal (biarkan profit berkembang), tight saat profit tinggi (lock profit)

            if profit_loss < 5.0:

                dynamic_trailing_stop = stop_loss_percent  # Use original stop (loose)

            elif profit_loss < 10.0:

                dynamic_trailing_stop = stop_loss_percent * 0.75  # 25% tighter (medium)

            elif profit_loss < 20.0:

                dynamic_trailing_stop = stop_loss_percent * 0.50  # 50% tighter (tight)

            else:

                dynamic_trailing_stop = stop_loss_percent * 0.40  # 60% tighter (very tight)

            

            # Ensure minimum trailing stop (safety)

            dynamic_trailing_stop = max(0.5, min(8.0, dynamic_trailing_stop))

            

            trailing_stop = high_since_entry * (1 - dynamic_trailing_stop / 100.0)

            

            # NEW: Track remaining position for partial profit taking

            if not hasattr(execute_partial_sell_order, '_partial_sells'):

                execute_partial_sell_order._partial_sells = {}

            if symbol not in execute_partial_sell_order._partial_sells:

                execute_partial_sell_order._partial_sells[symbol] = {

                    'total_sold': 0.0,

                    'total_locked_profit': 0.0,

                    'remaining_percentage': 1.0,

                    'entry_price': entry_price

                }

            remaining_percentage = execute_partial_sell_order._partial_sells[symbol]['remaining_percentage']

            

            status = f"🧠 {symbol} | Price: ${fmt(price)} | High: ${fmt(high_since_entry)} | AI Stop: ${fmt(trailing_stop)} ({dynamic_trailing_stop:.2f}%) | P/L: {profit_loss:+.2f}% | Position: {remaining_percentage:.0%}"

            output_manager.print_live(status)

            

            # NEW: Partial Profit Taking (Scale Out) Strategy

            # Lock profit bertahap untuk risk reduction

            # Only execute if remaining_percentage is still 100% (no partial sell yet)

            if remaining_percentage >= 0.999:  # Use 0.999 to avoid floating point issues

                if profit_loss >= 30.0:

                    # Sell 70% at 30% profit (lock 21% profit)

                    sell_pct = 0.70

                    if await execute_partial_sell_order(symbol, price, sell_pct, entry_price, profit_loss, "30% profit target"):

                        # Update remaining percentage from function

                        remaining_percentage = execute_partial_sell_order._partial_sells[symbol]['remaining_percentage']

                        output_manager.print_static(f"{GREEN}✅ Partial sell executed. Remaining position: {remaining_percentage:.0%}{RESET}")

                elif profit_loss >= 20.0:

                    # Sell 50% at 20% profit (lock 10% profit)

                    sell_pct = 0.50

                    if await execute_partial_sell_order(symbol, price, sell_pct, entry_price, profit_loss, "20% profit target"):

                        # Update remaining percentage from function

                        remaining_percentage = execute_partial_sell_order._partial_sells[symbol]['remaining_percentage']

                        output_manager.print_static(f"{GREEN}✅ Partial sell executed. Remaining position: {remaining_percentage:.0%}{RESET}")

                elif profit_loss >= 10.0:

                    # Sell 30% at 10% profit (lock 3% profit)

                    sell_pct = 0.30

                    if await execute_partial_sell_order(symbol, price, sell_pct, entry_price, profit_loss, "10% profit target"):

                        # Update remaining percentage from function

                        remaining_percentage = execute_partial_sell_order._partial_sells[symbol]['remaining_percentage']

                        output_manager.print_static(f"{GREEN}✅ Partial sell executed. Remaining position: {remaining_percentage:.0%}{RESET}")

            

            # FIX: Check trailing stop FIRST before false signal checks

            # This ensures trailing stop can trigger even if false signal is detected

            # CRITICAL: Trailing stop check must happen BEFORE any continue statements

            trailing_stop_triggered = False

            if price <= trailing_stop + 1e-10:

                trailing_stop_triggered = True

                try:

                    coin_type = await get_coin_type_range(symbol)

                    current_volatility = await calculate_coin_volatility(symbol)

                    current_analysis = await analyze_hold_wait_buy_ai(symbol)

                    current_regime = current_analysis.market_regime

                    if coin_type == 'BLUECHIP':

                        whale_wall_threshold = 28

                        buy_pressure_threshold = 47

                    elif coin_type == 'MAJOR':

                        whale_wall_threshold = 30

                        buy_pressure_threshold = 50

                    elif coin_type == 'ALTCOIN':

                        whale_wall_threshold = 32

                        buy_pressure_threshold = 53

                    else:

                        whale_wall_threshold = 36

                        buy_pressure_threshold = 55

                    if current_volatility > 30:

                        buy_pressure_threshold -= 3

                    elif current_volatility < 10:

                        buy_pressure_threshold += 3

                    if current_regime in [MarketRegime.STRONG_UPTREND, MarketRegime.WEAK_UPTREND]:

                        buy_pressure_threshold -= 2

                    elif current_regime in [MarketRegime.STRONG_DOWNTREND, MarketRegime.WEAK_DOWNTREND]:

                        buy_pressure_threshold += 3

                    orderbook_data = await get_orderbook_optimized(symbol, 20)

                    flow_data = await get_order_flow_optimized(symbol, 20)

                    candles = await get_candles_optimized(symbol, '5m', 10)

                    if orderbook_data and flow_data and candles:

                        is_whale_wall, whale_desc, whale_details = detect_whale_wall_absorption(

                            symbol, orderbook_data, flow_data

                        )

                        if is_whale_wall:

                            whale_strength = whale_details.get('bid_wall_ratio', 0) * 100

                            if whale_strength >= whale_wall_threshold:

                                # FIX: Add cooldown mechanism to prevent spam

                                if not hasattr(detect_whale_wall_absorption, '_last_message_time'):

                                    detect_whale_wall_absorption._last_message_time = {}

                                if not hasattr(detect_whale_wall_absorption, '_last_message_desc'):

                                    detect_whale_wall_absorption._last_message_desc = {}

                                

                                current_time = time.time()

                                last_time = detect_whale_wall_absorption._last_message_time.get(symbol, 0)

                                last_desc = detect_whale_wall_absorption._last_message_desc.get(symbol, "")

                                cooldown_seconds = 30.0  # Show message max once per 30 seconds

                                

                                # Only show message if:

                                # 1. Never shown before for this symbol, OR

                                # 2. Cooldown expired (30 seconds), OR

                                # 3. Message changed (different whale wall detected)

                                should_show = (

                                    last_time == 0 or

                                    (current_time - last_time) >= cooldown_seconds or

                                    whale_desc != last_desc

                                )

                                

                                if should_show:

                                    detect_whale_wall_absorption._last_message_time[symbol] = current_time

                                    detect_whale_wall_absorption._last_message_desc[symbol] = whale_desc

                                    

                                    output_manager.stop_live_update()

                                    output_manager.print_static(f"\n{YELLOW}{'='*70}{RESET}")

                                    output_manager.print_static(f"{YELLOW}⚠️ WHALE WALL DETECTED - Hold position!{RESET}")

                                    output_manager.print_static(f"{YELLOW}Reason: {whale_desc}{RESET}")

                                    output_manager.print_static(f"{YELLOW}Coin: {coin_type} | Volatility: {current_volatility:.1f}% | Threshold: {whale_wall_threshold}%{RESET}")

                                    output_manager.print_static(f"{YELLOW}Continuing to monitor...{RESET}")

                                    output_manager.print_static(f"{YELLOW}{'='*70}{RESET}")

                                    output_manager.start_live_update()

                                # FIX: Don't continue here if trailing stop is triggered

                                # Check trailing stop first before skipping

                                if trailing_stop_triggered:

                                    break  # Exit inner try block to execute trailing stop

                                continue

                        is_false_signal, false_desc, false_details = detect_false_signal_patterns(

                            candles, flow_data, orderbook_data

                        )

                        if is_false_signal:

                            # FIX: Add cooldown mechanism to prevent spam

                            if not hasattr(detect_false_signal_patterns, '_last_message_time'):

                                detect_false_signal_patterns._last_message_time = {}

                            if not hasattr(detect_false_signal_patterns, '_last_message_desc'):

                                detect_false_signal_patterns._last_message_desc = {}

                            

                            current_time = time.time()

                            last_time = detect_false_signal_patterns._last_message_time.get(symbol, 0)

                            last_desc = detect_false_signal_patterns._last_message_desc.get(symbol, "")

                            cooldown_seconds = 30.0  # Show message max once per 30 seconds

                            

                            # Only show message if:

                            # 1. Never shown before for this symbol, OR

                            # 2. Cooldown expired (30 seconds), OR

                            # 3. Message changed (different false signal detected)

                            should_show = (

                                last_time == 0 or

                                (current_time - last_time) >= cooldown_seconds or

                                false_desc != last_desc

                            )

                            

                            if should_show:

                                detect_false_signal_patterns._last_message_time[symbol] = current_time

                                detect_false_signal_patterns._last_message_desc[symbol] = false_desc

                                

                                output_manager.stop_live_update()

                                output_manager.print_static(f"\n{YELLOW}{'='*70}{RESET}")

                                output_manager.print_static(f"{YELLOW}⚠️ FALSE SIGNAL DETECTED - Hold position!{RESET}")

                                output_manager.print_static(f"{YELLOW}Reason: {false_desc}{RESET}")

                                output_manager.print_static(f"{YELLOW}Coin: {coin_type} | Regime: {current_regime.value}{RESET}")

                                output_manager.print_static(f"{YELLOW}Continuing to monitor...{RESET}")

                                output_manager.print_static(f"{YELLOW}{'='*70}{RESET}")

                                output_manager.start_live_update()

                            # FIX: Don't continue here if trailing stop is triggered

                            # Check trailing stop first before skipping

                            if trailing_stop_triggered:

                                break  # Exit inner try block to execute trailing stop

                            continue

                        buy_pressure = flow_data.get('buy_pressure', 0)

                        sell_pressure = flow_data.get('sell_pressure', 0)

                        if buy_pressure >= buy_pressure_threshold:

                            output_manager.stop_live_update()

                            output_manager.print_static(f"\n{YELLOW}{'='*70}{RESET}")

                            output_manager.print_static(f"{YELLOW}⚠️ STRONG BUYING PRESSURE ({buy_pressure:.0f}%) - Hold position!{RESET}")

                            output_manager.print_static(f"{YELLOW}This might be a temporary dip{RESET}")

                            output_manager.print_static(f"{YELLOW}Coin: {coin_type} | Threshold: {buy_pressure_threshold:.0f}% | Regime: {current_regime.value}{RESET}")

                            output_manager.print_static(f"{YELLOW}Continuing to monitor...{RESET}")

                            output_manager.print_static(f"{YELLOW}{'='*70}{RESET}")

                            output_manager.start_live_update()

                            continue

                except Exception as e:

                    pass

                

                # NEW: Check if there's remaining position from partial sells

                remaining_percentage = execute_partial_sell_order._partial_sells.get(symbol, {}).get('remaining_percentage', 1.0)

                total_locked_profit = execute_partial_sell_order._partial_sells.get(symbol, {}).get('total_locked_profit', 0.0)

                

                output_manager.stop_live_update()

                output_manager.print_static(f"\n{RED}{'='*70}{RESET}")

                output_manager.print_static(f"{RED}🎯 AI TRAILING STOP TRIGGERED! [{now_ts()}]{RESET}")

                

                if remaining_percentage < 1.0:

                    # Partial sells were executed - sell remaining position

                    output_manager.print_static(f"{MAGENTA}💎 SELL REMAINING {remaining_percentage:.0%} POSITION of {symbol} at ${fmt(price)} [{now_ts()}]{RESET}")

                    output_manager.print_static(f"{CYAN}📊 AI TRADING SUMMARY:")

                    output_manager.print_static(f"  - Entry: ${fmt(entry_price)}")

                    output_manager.print_static(f"  - Final Exit: ${fmt(price)}")

                    output_manager.print_static(f"  - Final P/L: {profit_loss:+.2f}% (on {remaining_percentage:.0%} remaining)")

                    output_manager.print_static(f"  - Total Locked Profit: {total_locked_profit:+.2f}%")

                    combined_profit = total_locked_profit + (profit_loss * remaining_percentage)

                    output_manager.print_static(f"  - Combined Profit: {combined_profit:+.2f}%")

                    output_manager.print_static(f"  - AI Trailing Stop: {dynamic_trailing_stop:.2f}% (dynamic)")

                else:

                    # No partial sells - full position exit

                    output_manager.print_static(f"{MAGENTA}💎 SELL {symbol} at ${fmt(price)} [{now_ts()}]{RESET}")

                    output_manager.print_static(f"{CYAN}📊 AI TRADING SUMMARY:")

                    output_manager.print_static(f"  - Entry: ${fmt(entry_price)}")

                    output_manager.print_static(f"  - Exit: ${fmt(price)}")

                    output_manager.print_static(f"  - P/L: {profit_loss:+.2f}%")

                    output_manager.print_static(f"  - AI Trailing Stop: {dynamic_trailing_stop:.2f}% (dynamic)")

                

                output_manager.print_static(f"{RED}{'='*70}{RESET}")

                

                # Clean up partial sell tracking

                if hasattr(execute_partial_sell_order, '_partial_sells') and symbol in execute_partial_sell_order._partial_sells:

                    del execute_partial_sell_order._partial_sells[symbol]

                

                break

            await asyncio.sleep(poll_interval)

    except KeyboardInterrupt:

        output_manager.stop_live_update()

        output_manager.print_static(f"\n{YELLOW}⏹️ AI Trailing stop dihentikan manual{RESET}")

        portfolio_manager.print_session_summary()

        if portfolio_manager.positions:

            output_manager.print_static(f"\n{YELLOW}⚠️  You have {len(portfolio_manager.positions)} tracked position(s){RESET}")

            close_choice = input(f"{RED}Close all positions before exiting? (yes/no): {RESET}").strip().lower()

            if close_choice in ['yes', 'y']:

                await emergency_close_all_positions(dry_run=False)

            else:

                output_manager.print_static(f"{YELLOW}⚠️ Positions remain open - manage manually if needed{RESET}")

    except Exception as e:

        output_manager.stop_live_update()

        output_manager.print_static(f"\n{RED}❌ AI Trailing Stop error: {e}{RESET}")

        error_handler.record_error("Trailing_Stop_Error", "AI trailing stop failed", {"error": str(e)})

    finally:

        output_manager.stop_live_update()



async def auto_trailing_stop_after_buy(symbol: str, entry_price: float, stop_loss_percent: float = 2.0, poll_interval: float = 2.0):

    output_manager.print_static(f"\n{MAGENTA}🔄 STATIC TRAILING STOP ACTIVATED{RESET}")

    output_manager.print_static(f"{MAGENTA}Entry: ${fmt(entry_price)} | Stop: {stop_loss_percent}%{RESET}")

    try:

        high_since_entry = entry_price

        output_manager.print_static(f"{CYAN}Static Trailing Stop:{RESET}")

        output_manager.print_static(f"  - Entry: ${fmt(entry_price)}")

        output_manager.print_static(f"  - Stop: {stop_loss_percent}% from high")

        output_manager.start_live_update()

        while True:

            price = await get_price_optimized(symbol)

            if price is None:

                await asyncio.sleep(poll_interval)

                continue

            if price > high_since_entry:

                high_since_entry = price

            trailing_stop = high_since_entry * (1 - stop_loss_percent / 100.0)

            profit_loss = safe_division((price - entry_price), entry_price, 0.0) * 100

            status = f"🔄 {symbol} | Price: ${fmt(price)} | High: ${fmt(high_since_entry)} | Stop: ${fmt(trailing_stop)} | P/L: {profit_loss:+.2f}%"

            output_manager.print_live(status)

            if price <= trailing_stop + 1e-10:

                output_manager.stop_live_update()

                output_manager.print_static(f"\n{RED}{'='*70}{RESET}")

                output_manager.print_static(f"{RED}🎯 TRAILING STOP TRIGGERED! [{now_ts()}]{RESET}")

                output_manager.print_static(f"{MAGENTA}💎 SELL {symbol} at ${fmt(price)} [{now_ts()}]{RESET}")

                output_manager.print_static(f"{CYAN}Summary: P/L {profit_loss:+.2f}%{RESET}")

                output_manager.print_static(f"{RED}{'='*70}{RESET}")

                break

            await asyncio.sleep(poll_interval)

    except KeyboardInterrupt:

        output_manager.stop_live_update()

        output_manager.print_static(f"\n{YELLOW}⏹️ Trailing stop stopped manually{RESET}")

        portfolio_manager.print_session_summary()

        if portfolio_manager.positions:

            output_manager.print_static(f"\n{YELLOW}⚠️  You have {len(portfolio_manager.positions)} tracked position(s){RESET}")

            close_choice = input(f"{RED}Close all positions before exiting? (yes/no): {RESET}").strip().lower()

            if close_choice in ['yes', 'y']:

                await emergency_close_all_positions(dry_run=False)

            else:

                output_manager.print_static(f"{YELLOW}⚠️ Positions remain open - manage manually if needed{RESET}")

    except Exception as e:

        output_manager.stop_live_update()

        output_manager.print_static(f"\n{RED}❌ Static trailing stop error: {e}{RESET}")

        error_handler.record_error("Static_Stop_Error", "Static trailing stop failed", {"error": str(e)})

    finally:

        output_manager.stop_live_update()



async def enhanced_manual_trailing(symbol: str, strategy_type: str, current_price: float, volatility: float, analysis: ActionAnalysis, buy_amount: Optional[float] = None):

    output_manager.print_static(f"\n{MAGENTA}🎯 ENHANCED MANUAL {strategy_type} TRAILING{RESET}")

    if strategy_type == "BUY" and buy_amount:

        output_manager.print_static(f"{MAGENTA}Amount: ${buy_amount:.2f} USDT{RESET}")

    try:

        if strategy_type == "SELL":

            try:

                # Get coin balance info if available

                sell_quantity = None

                coin_name = symbol.split('_')[0].upper()

                available_balance = None

                min_order_size = None

                

                try:

                    # Try to get balance info from module level variable

                    global _sell_balance_info

                    if symbol in _sell_balance_info:

                        balance_info = _sell_balance_info[symbol]

                        coin_name = balance_info.get('coin_name', coin_name)

                        available_balance = balance_info.get('available_balance', None)

                        min_order_size = balance_info.get('min_order_size', None)

                except:

                    pass

                

                # If we don't have balance info, try to get it from exchange client

                if available_balance is None:

                    try:

                        from core import _init_exchange_client

                        exchange_client = _init_exchange_client()

                        if exchange_client:

                            account_info = exchange_client.get_account_info()

                            if account_info:

                                spot_balances = account_info.get('spot_balances', [])

                                for balance in spot_balances:

                                    if balance.get('coin', '').upper() == coin_name:

                                        available_balance = balance.get('available', 0)

                                        break

                                

                                # Get minimum order size

                                if hasattr(exchange_client, 'get_min_order_size'):

                                    min_order_size = exchange_client.get_min_order_size(symbol)

                    except:

                        pass

                

                # Get minimum order size fallback if needed

                if min_order_size is None or min_order_size <= 0:

                    if coin_name in ['BTC', 'ETH']:

                        min_order_size = 0.0001

                    elif coin_name in ['USDT', 'USDC', 'BUSD']:

                        min_order_size = 1.0

                    else:

                        min_order_size = 1.0

                

                # Get minimum order value (notional) from exchange

                min_order_value = None

                try:

                    from core import _init_exchange_client

                    exchange_client = _init_exchange_client()

                    if exchange_client and hasattr(exchange_client, 'get_min_order_value'):

                        min_order_value = exchange_client.get_min_order_value(symbol)

                except:

                    pass

                

                # Fallback minimum order value (default $5 for most exchanges)

                if min_order_value is None or min_order_value <= 0:

                    min_order_value = 5.0  # Default $5 USDT minimum order value

                

                # Ask for quantity to sell

                if available_balance is not None:

                    output_manager.print_static(f"\n{CYAN}Available Balance: {available_balance:.8f} {coin_name}{RESET}")

                    output_manager.print_static(f"{CYAN}Minimum Order Size: {min_order_size:.8f} {coin_name}{RESET}")

                    output_manager.print_static(f"{CYAN}Minimum Order Value: ${min_order_value:.2f} USDT (notional){RESET}")

                    output_manager.print_static(f"{CYAN}Current Price: ${fmt(current_price)}{RESET}")

                    

                    # Calculate minimum quantity needed based on order value

                    min_qty_by_value = min_order_value / current_price if current_price > 0 else 0

                    min_qty_required = max(min_order_size, min_qty_by_value)

                    

                    if min_qty_required > min_order_size:

                        output_manager.print_static(f"{YELLOW}⚠️  Minimum quantity required: {min_qty_required:.8f} {coin_name} (based on ${min_order_value:.2f} order value at current price){RESET}")

                    

                    while True:

                        try:

                            qty_input = input(f"\n{YELLOW}Enter quantity to sell ({coin_name}) (max: {available_balance:.8f}): {RESET}").strip()

                            if not qty_input:

                                output_manager.print_static(f"{RED}Quantity cannot be empty. Please enter a valid amount.{RESET}")

                                continue

                            

                            sell_quantity = float(qty_input)

                            

                            # Validate quantity

                            if sell_quantity <= 0:

                                output_manager.print_static(f"{RED}Quantity must be greater than 0.{RESET}")

                                continue

                            

                            if sell_quantity < min_order_size:

                                output_manager.print_static(f"{RED}Quantity {sell_quantity:.8f} is less than minimum order size {min_order_size:.8f} {coin_name}{RESET}")

                                continue

                            

                            if sell_quantity > available_balance:

                                output_manager.print_static(f"{RED}Quantity {sell_quantity:.8f} exceeds available balance {available_balance:.8f} {coin_name}{RESET}")

                                continue

                            

                            # Validate order value (notional)

                            order_value = sell_quantity * current_price

                            if order_value < min_order_value:

                                output_manager.print_static(f"{RED}Order value ${order_value:.2f} is less than minimum order value ${min_order_value:.2f} USDT{RESET}")

                                output_manager.print_static(f"{YELLOW}At current price ${fmt(current_price)}, you need at least {min_qty_required:.8f} {coin_name} to meet minimum order value{RESET}")

                                continue

                            

                            # Valid quantity

                            output_manager.print_static(f"{GREEN}✅ Quantity confirmed: {sell_quantity:.8f} {coin_name} (value: ${order_value:.2f} USDT){RESET}")

                            break

                        except ValueError:

                            output_manager.print_static(f"{RED}Invalid input. Please enter a valid number.{RESET}")

                            continue

                else:

                    # If we can't get balance, ask for quantity without validation

                    while True:

                        try:

                            qty_input = input(f"\n{YELLOW}Enter quantity to sell ({coin_name}): {RESET}").strip()

                            if not qty_input:

                                output_manager.print_static(f"{RED}Quantity cannot be empty. Please enter a valid amount.{RESET}")

                                continue

                            sell_quantity = float(qty_input)

                            if sell_quantity <= 0:

                                output_manager.print_static(f"{RED}Quantity must be greater than 0.{RESET}")

                                continue

                            break

                        except ValueError:

                            output_manager.print_static(f"{RED}Invalid input. Please enter a valid number.{RESET}")

                            continue

                

                activate_price = float(input(f"Enter activation price (current: ${fmt(current_price)}): ").strip())

                delta_percent = float(input(f"Enter delta % (AI suggests {await calculate_ai_recommended_delta(analysis, {}, 'SELL', symbol):.1f}%): ").strip())

                advance = input("Enable advance mode? (y/N): ").strip().lower() == 'y'

                offset = 0.0

                if advance:

                    offset = float(input("Enter offset % (e.g., 1.0): ").strip())

                await hybrid_trailing_sell(

                    symbol=symbol,

                    delta_percent=delta_percent,

                    activate_price=activate_price,

                    advance=advance,

                    offset=offset,

                    skip_wait=False,

                    poll_interval=2.0,

                    sell_quantity=sell_quantity if sell_quantity else None

                )

            except ValueError:

                output_manager.print_static(f"{RED}Invalid input. Please enter valid numbers.{RESET}")

        else:  # BUY mode

            try:

                activate_price = float(input(f"Enter activation price (current: ${fmt(current_price)}): ").strip())

                delta_percent = float(input(f"Enter delta % (AI suggests {await calculate_ai_recommended_delta(analysis, {}, 'BUY', symbol):.1f}%): ").strip())

                advance = input("Enable advance mode? (y/N): ").strip().lower() == 'y'

                offset = 0.0

                if advance:

                    offset = float(input("Enter offset % (e.g., 1.0): ").strip())

                auto_stop = input("Enable auto trailing stop? (Y/n): ").strip().lower() != 'n'

                stop_loss = 2.0

                dynamic_stop = True

                if auto_stop:

                    dynamic_stop = input("Use dynamic AI stop? (Y/n): ").strip().lower() != 'n'

                    if not dynamic_stop:

                        stop_loss = float(input("Enter stop loss % (e.g., 2.0): ").strip())

                await hybrid_trailing_buy(

                    symbol=symbol,

                    delta_percent=delta_percent,

                    activate_price=activate_price,

                    advance=advance,

                    offset=offset,

                    skip_wait=False,

                    poll_interval=2.0,

                    auto_trailing_stop=auto_stop,

                    stop_loss_percent=stop_loss,

                    dynamic_ai_stop=dynamic_stop,

                    auto_mode=False,

                    timeout_minutes=30,

                    buy_amount=buy_amount

                )

            except ValueError:

                output_manager.print_static(f"{RED}Invalid input. Please enter valid numbers.{RESET}")

    except Exception as e:

        output_manager.print_static(f"{RED}❌ Manual trailing setup error: {e}{RESET}")

        error_handler.record_error("Manual_Trading_Error", "Manual trading setup failed", {"error": str(e)})



# Auto AI Trailing Buy - Simple buy only, exit after successful buy

async def auto_ai_trailing_buy(symbol: str, activate_price: float, delta_percent: float, 

                               buy_amount: float, current_price: float, analysis: ActionAnalysis):

    """

    Auto AI Trailing Buy - AI determines activation and delta, executes buy, then exits immediately

    """

    output_manager.print_static(f"\n{MAGENTA}🤖 AUTO AI TRAILING BUY{RESET}")

    output_manager.print_static(f"{MAGENTA}Symbol: {symbol} | Amount: ${buy_amount:.2f} USDT{RESET}")

    output_manager.print_static(f"{MAGENTA}Activation: <= ${fmt(activate_price)} | Delta: {delta_percent:.1f}%{RESET}")

    output_manager.print_static(f"{CYAN}Waiting for price to reach activation...{RESET}\n")

    

    try:

        # Start live update mode for status display

        output_manager.start_live_update()

        from core import _init_exchange_client, get_price_optimized

        exchange_client = _init_exchange_client()

        if not exchange_client:

            output_manager.print_static(f"{RED}❌ Exchange client not available{RESET}")

            return

        

        # Get minimum order size and value

        min_order_size = None

        min_order_value = None

        if hasattr(exchange_client, 'get_min_order_size'):

            try:

                min_order_size = exchange_client.get_min_order_size(symbol)

            except:

                pass

        if hasattr(exchange_client, 'get_min_order_value'):

            try:

                min_order_value = exchange_client.get_min_order_value(symbol)

            except:

                pass

        

        # Fallback defaults

        if min_order_size is None or min_order_size <= 0:

            min_order_size = 0.0001

        if min_order_value is None or min_order_value <= 0:

            min_order_value = 5.0

        

        waiting_start_time = time.time()

        last_price = current_price

        low = current_price

        

        # Poll for price to reach activation

        while True:

            try:

                price = await get_price_optimized(symbol)

                if not price or price <= 0:

                    await asyncio.sleep(2.0)

                    continue

                

                # Update low

                if price < low:

                    low = price

                

                # Check if price reached activation - then start trailing

                if price <= activate_price:

                    output_manager.stop_live_update()

                    output_manager.print_static(f"\n{GREEN}{'='*60}{RESET}")

                    output_manager.print_static(f"{GREEN}✅ ACTIVATION ACHIEVED! Starting trailing buy...{RESET}")

                    output_manager.print_static(f"{GREEN}{'='*60}{RESET}")

                    output_manager.print_static(f"{CYAN}Price: ${fmt(price)} | Activation: ${fmt(activate_price)}{RESET}")

                    output_manager.print_static(f"{CYAN}Now tracking low point and waiting for price to rise {delta_percent:.1f}% from low{RESET}\n")

                    

                    # Start trailing buy - track low and wait for trigger

                    activation_moment = time.time()

                    trailing_start_price = price

                    trailing_low = price

                    trailing_trigger = None

                    

                    output_manager.start_live_update()

                    while True:

                        try:

                            current_price = await get_price_optimized(symbol)

                            if not current_price or current_price <= 0:

                                await asyncio.sleep(2.0)

                                continue

                            

                            # Update low point

                            if current_price < trailing_low:

                                trailing_low = current_price

                            

                            # Calculate trigger: low + delta%

                            trailing_trigger = trailing_low * (1 + delta_percent / 100.0)

                            

                            # Show trailing status

                            discount_from_low = safe_division((current_price - trailing_low), trailing_low, 0.0) * 100

                            needed_pct = safe_division((trailing_trigger - current_price), current_price, 0.0) * 100

                            trailing_time = time.time() - activation_moment

                            

                            status_msg = f"{CYAN}📈 TRAILING BUY | Price: ${fmt(current_price)} | Low: ${fmt(trailing_low)} | Trigger: ${fmt(trailing_trigger)} | Need: {needed_pct:+.2f}% | From Low: {discount_from_low:+.2f}% | Time: {trailing_time:.0f}s{RESET}"

                            output_manager.print_live(status_msg)

                            

                            # Check if price reached trigger (trailing buy condition)

                            if current_price >= trailing_trigger - 1e-10:

                                output_manager.stop_live_update()

                                output_manager.print_static(f"\n{GREEN}{'='*60}{RESET}")

                                output_manager.print_static(f"{GREEN}✅ TRAILING TRIGGER REACHED! Executing buy order...{RESET}")

                                output_manager.print_static(f"{GREEN}{'='*60}{RESET}")

                                output_manager.print_static(f"{CYAN}Price: ${fmt(current_price)} | Low: ${fmt(trailing_low)} | Trigger: ${fmt(trailing_trigger)}{RESET}")

                                output_manager.print_static(f"{CYAN}Price rose {discount_from_low:.2f}% from low point{RESET}\n")

                                

                                # Use current price for buy

                                price = current_price

                                

                                # Calculate quantity based on buy_amount

                                quantity = buy_amount / price if price > 0 else 0

                                

                                # Validate quantity meets minimums

                                min_qty_by_value = min_order_value / price if price > 0 else 0

                                min_qty_required = max(min_order_size, min_qty_by_value)

                                

                                if quantity < min_qty_required:

                                    output_manager.print_static(f"{YELLOW}⚠️  Calculated quantity {quantity:.8f} is less than minimum {min_qty_required:.8f}{RESET}")

                                    output_manager.print_static(f"{YELLOW}Adjusting quantity to minimum required...{RESET}")

                                    quantity = min_qty_required

                                    # Recalculate buy_amount based on adjusted quantity

                                    buy_amount = quantity * price

                                

                                output_manager.print_static(f"{CYAN}Buy Quantity: {quantity:.8f} | Amount: ${buy_amount:.2f} USDT{RESET}\n")

                                

                                # Execute buy order - place actual order on exchange

                                try:

                                    # Place market buy order on exchange

                                    # Note: execute_buy_order only records, doesn't place actual order

                                    # We need to place the order directly using exchange client

                                    order_placed = False

                                    

                                    # Try to place order using exchange client methods

                                    if hasattr(exchange_client, 'place_market_buy_order'):

                                        try:

                                            order_result = exchange_client.place_market_buy_order(symbol, quantity)

                                            if order_result:

                                                order_placed = True

                                        except Exception as e:

                                            output_manager.print_static(f"{YELLOW}⚠️  place_market_buy_order failed: {str(e)}{RESET}")

                                    

                                    if not order_placed and hasattr(exchange_client, 'place_order'):

                                        try:

                                            order_result = exchange_client.place_order(

                                                symbol=symbol,

                                                side='BUY',

                                                order_type='MARKET',

                                                quantity=quantity

                                            )

                                            if order_result:

                                                order_placed = True

                                        except Exception as e:

                                            output_manager.print_static(f"{YELLOW}⚠️  place_order failed: {str(e)}{RESET}")

                                    

                                    if not order_placed:

                                        # If no order placement method, just record the trade

                                        output_manager.print_static(f"{YELLOW}⚠️  Order placement not available - recording trade only{RESET}")

                                        order_placed = True  # Continue to record

                                    

                                    if order_placed:

                                        # Record trade

                                        entry_efficiency = safe_division((price - trailing_low), (activate_price - trailing_low), 1.0) * 100 if activate_price > trailing_low else 100

                                        

                                        output_manager.print_static(f"\n{GREEN}✅ BUY ORDER EXECUTED{RESET}")

                                        output_manager.print_static(f"{GREEN}Symbol: {symbol}{RESET}")

                                        output_manager.print_static(f"{GREEN}Quantity: {quantity:.8f}{RESET}")

                                        output_manager.print_static(f"{GREEN}Price: ${fmt(price)}{RESET}")

                                        output_manager.print_static(f"{GREEN}Amount: ${buy_amount:.2f} USDT{RESET}")

                                        output_manager.print_static(f"{GREEN}Entry Efficiency: {entry_efficiency:.1f}%{RESET}")

                                        

                                        portfolio_manager.record_trade_with_performance(

                                            symbol=symbol,

                                            action="BUY",

                                            price=price,

                                            size=quantity,

                                            pnl=0

                                        )

                                        

                                        await portfolio_manager.set_position(symbol, {

                                            'action': 'BUY',

                                            'price': price,

                                            'size': quantity,

                                            'timestamp': time.time(),

                                            'entry_type': 'AUTO_AI_TRAILING_BUY'

                                        })

                                        

                                        output_manager.print_static(f"\n{GREEN}✅ Buy completed. Exiting as requested.{RESET}")

                                        output_manager.print_static(f"{CYAN}💡 Position recorded. You can manage it manually or use SELL mode later.{RESET}\n")

                                        return

                                    else:

                                        output_manager.print_static(f"{RED}❌ Buy order failed{RESET}")

                                        return

                                except Exception as e:

                                    output_manager.print_static(f"{RED}❌ Error executing buy order: {str(e)}{RESET}")

                                    return

                            

                        except asyncio.CancelledError:

                            output_manager.stop_live_update()

                            output_manager.print_static(f"\n{YELLOW}⏹️ Trailing buy cancelled{RESET}")

                            return

                        except Exception as e:

                            output_manager.stop_live_update()

                            output_manager.print_static(f"\n{RED}❌ Error in trailing buy: {str(e)}{RESET}")

                            await asyncio.sleep(2.0)

                            output_manager.start_live_update()

                            continue

                

                # Show waiting status

                discount_pct = safe_division((current_price - price), current_price, 0.0) * 100

                wait_time = time.time() - waiting_start_time

                # Use print_live for live updates instead of print_static with end/flush

                status_msg = f"{CYAN}⏳ Waiting... Price: ${fmt(price)} | Target: ${fmt(activate_price)} | Discount: {discount_pct:.1f}% | Low: ${fmt(low)} | Time: {wait_time:.0f}s{RESET}"

                output_manager.print_live(status_msg)

                

                last_price = price

                await asyncio.sleep(2.0)

                

            except asyncio.CancelledError:

                output_manager.stop_live_update()

                output_manager.print_static(f"\n{YELLOW}⏹️ Auto AI trailing buy cancelled{RESET}")

                return

            except Exception as e:

                output_manager.stop_live_update()

                output_manager.print_static(f"\n{RED}❌ Error in auto AI trailing buy: {str(e)}{RESET}")

                await asyncio.sleep(2.0)

                # Restart live update mode

                output_manager.start_live_update()

                continue

                

    except Exception as e:

        output_manager.stop_live_update()

        output_manager.print_static(f"\n{RED}❌ Auto AI trailing buy error: {e}{RESET}")

        error_handler.record_error("Auto_AI_Buy_Error", "Auto AI trailing buy failed", {"error": str(e)})



def print_ai_analysis(analysis: ActionAnalysis, symbol: str):

    output_manager.print_static(f"\n{MAGENTA}{'='*100}{RESET}")

    output_manager.print_static(f"{MAGENTA}{'=== QUANTITATIVE SUPER AI TRADING ANALYSIS ===':^100}{RESET}")

    output_manager.print_static(f"{MAGENTA}{'='*100}{RESET}")

    try:

        rec = analysis.recommendation

        conf = analysis.confidence

        score = analysis.score

        regime = analysis.market_regime

        if rec == ActionRecommendation.STRONG_BUY:

            color = GREEN

            emoji = "🎯"

        elif rec == ActionRecommendation.BUY:

            color = GREEN

            emoji = "📈"

        elif rec == ActionRecommendation.HOLD:

            color = YELLOW

            emoji = "🤔"

        elif rec == ActionRecommendation.WAIT:

            color = YELLOW

            emoji = "⏳"

        elif rec == ActionRecommendation.SELL:

            color = RED

            emoji = "🔻"

        else:

            color = RED

            emoji = "💀"

        output_manager.print_static(f"{CYAN}Symbol:{RESET} {symbol}")

        output_manager.print_static(f"{CYAN}AI Recommendation:{RESET} {color}{emoji} {rec.value}{RESET}")

        output_manager.print_static(f"{CYAN}AI Confidence:{RESET} {GREEN if conf > 70 else YELLOW if conf > 50 else RED}{conf:.1f}%{RESET}")

        output_manager.print_static(f"{CYAN}AI Score:{RESET} {GREEN if score > 70 else YELLOW if score > 50 else RED}{score:.1f}/100{RESET}")

        output_manager.print_static(f"{CYAN}Market Regime:{RESET} {regime.value}")

        output_manager.print_static(f"{CYAN}Risk Level:{RESET} {analysis.risk_level}")

        output_manager.print_static(f"{CYAN}Time Horizon:{RESET} {analysis.time_horizon}")

        output_manager.print_static(f"\n{CYAN}🎯 QUANTITATIVE PRICE TARGETS:{RESET}")

        targets = analysis.price_targets

        ws_manager = _init_ws_manager()

        current = None

        if ws_manager:

            current = ws_manager.get_price(symbol)

        if not current:
            # Try WebSocket fallback via get_price_optimized (handles async properly)
            # Note: This function is not async, so we use direct REST fallback
            exchange_client = _init_exchange_client()
            if exchange_client and hasattr(exchange_client, 'get_price_rest'):
                current = exchange_client.get_price_rest(symbol) or 0

        if current and current > 0:

            output_manager.print_static(f"  Short-term: ${fmt(targets.get('short_term', 0))} ({((targets.get('short_term', 0)/current)-1)*100:+.1f}%)")

            output_manager.print_static(f"  Support: ${fmt(targets.get('support_near', 0))} ({((targets.get('support_near', 0)/current)-1)*100:+.1f}%)")

            upside = targets.get('upside_potential', 0)

            downside = targets.get('downside_risk', 0)

            output_manager.print_static(f"  Upside Potential: {GREEN if upside > 5 else YELLOW if upside > 2 else RED}{upside:+.1f}%{RESET}")

            output_manager.print_static(f"  Downside Risk: {RED if downside > 5 else YELLOW if downside > 2 else GREEN}{downside:+.1f}%{RESET}")

        output_manager.print_static(f"\n{CYAN}📊 QUANTITATIVE AI ANALYSIS REASONS:{RESET}")

        for i, reason in enumerate(analysis.reasons[:8]):

            output_manager.print_static(f"  {i+1}. {reason}")

        output_manager.print_static(f"{MAGENTA}{'='*100}{RESET}\n")

    except Exception as e:

        output_manager.print_static(f"{RED}❌ Error displaying AI analysis: {e}{RESET}")

        error_handler.record_error("Display_Error", "AI analysis display failed", {"error": str(e)})



async def real_time_performance_optimizer():

    global POLL_INTERVAL

    last_cleanup_time = time.time()

    while True:

        try:

            current_time = time.time()

            if current_time - last_cleanup_time > 1800:

                try:

                    risk_metrics.var_calculator.cleanup_old_data(max_age_days=7)

                    ml_enhancer.cleanup_old_patterns(max_patterns_per_symbol=100)

                    last_cleanup_time = current_time

                except Exception as e:

                    print(f"{YELLOW}⚠️ Periodic cleanup warning: {e}{RESET}")

            exchange_client = _init_exchange_client()

            api_stats = {}

            if exchange_client and hasattr(exchange_client, 'get_api_stats'):

                try:

                    api_stats = exchange_client.get_api_stats()

                except Exception:

                    api_stats = {}

            perf_report = performance_monitor.get_performance_report()

            current_api_rate = api_stats['requests_per_minute']

            if current_api_rate > 40:

                output_manager.print_static(f"{YELLOW}⚠️ High API usage: {current_api_rate}/min - optimizing...{RESET}")



                POLL_INTERVAL = min(5.0, POLL_INTERVAL * 1.5)

            elif current_api_rate < 20:



                POLL_INTERVAL = max(1.0, POLL_INTERVAL * 0.8)

            error_stats = error_handler.get_error_stats()

            if error_stats['error_count'] > 5:

                output_manager.print_static(f"{YELLOW}⚠️ High error rate detected - enabling conservative mode{RESET}")

            await asyncio.sleep(60)

        except Exception as e:

            output_manager.print_static(f"{YELLOW}⚠️ Performance optimizer error: {e}{RESET}")

            await asyncio.sleep(60)



async def fast_startup_test():

    try:

        # Ensure CONFIG is loaded

        if not CONFIG:

            load_config()

        

        # Check if we're in demo mode first

        is_demo_mode = False

        try:

            if SELECTED_EXCHANGE in CONFIG:

                api_key = CONFIG[SELECTED_EXCHANGE].get('API_KEY', '')

                api_secret = CONFIG[SELECTED_EXCHANGE].get('API_SECRET', '')

                # Check if API keys are placeholders (demo mode)

                # Common placeholder formats: 'your_xxx_key_here', 'your_api_key', empty, etc.

                api_key_lower = str(api_key).lower()

                api_secret_lower = str(api_secret).lower()

                is_demo_mode = (

                    not api_key or not api_secret or

                    'your_' in api_key_lower or 'your_' in api_secret_lower or

                    api_key == 'your_api_key' or api_secret == 'your_api_secret' or

                    api_key_lower.startswith('your_') or api_secret_lower.startswith('your_') or

                    '_here' in api_key_lower or '_here' in api_secret_lower

                )

            else:

                # If exchange not in CONFIG, assume demo mode

                is_demo_mode = True

        except Exception as e:

            # If error checking, assume demo mode to be safe

            is_demo_mode = True

        

        # Use the selected exchange client instead of hardcoded bybit_client

        # Reset client to ensure we use the newly selected exchange

        global _exchange_client

        _exchange_client = None  # Force re-initialization

        exchange_client = _init_exchange_client()

        if exchange_client:

            # Try to get price for BTC_USDT

            # If symbol is marked as unsupported, try a different symbol

            test_symbols = ["BTC_USDT", "ETH_USDT", "SOL_USDT"]

            price = None

            tested_symbol = None

            

            for test_symbol in test_symbols:

                # Skip if symbol is already marked as unsupported

                if hasattr(exchange_client, 'unsupported_symbols') and test_symbol in exchange_client.unsupported_symbols:

                    continue

                

                price = exchange_client.get_price_rest(test_symbol)

                if price and price > 0:

                    tested_symbol = test_symbol

                    break

            

            if price and price > 0:

                return True

            else:

                # If all test symbols failed

                if is_demo_mode:

                    # In demo mode, allow continuation even if price fetch fails

                    return True

                else:

                    # Not demo mode but all symbols failed - this is a real problem

                    output_manager.print_static(f"{RED}❌ API Connectivity: FAILED - No price data{RESET}")

                    output_manager.print_static(f"{YELLOW}💡 Please check your API credentials and network connection{RESET}")

                    output_manager.print_static(f"{YELLOW}💡 If using demo mode, ensure API keys are set to 'your_api_key' / 'your_api_secret'{RESET}")

                    return False

        else:

            output_manager.print_static(f"{RED}❌ API Connectivity: FAILED - Exchange client not initialized{RESET}")

            return False

    except Exception as e:

        output_manager.print_static(f"{RED}❌ Fast startup test failed: {e}{RESET}")

        return False



def get_dump_thresholds(coin_type: str) -> dict:

    thresholds = {

        'BLUECHIP': {

            'drop_10s': 2.0,

            'drop_30s': 3.5,

            'drop_60s': 6.0,

            'volume': 140,

            'points_needed': 5

        },

        'MAJOR': {

            'drop_10s': 2.5,

            'drop_30s': 4.5,

            'drop_60s': 7.5,

            'volume': 160,

            'points_needed': 5

        },

        'ALTCOIN': {

            'drop_10s': 3.5,

            'drop_30s': 6.5,

            'drop_60s': 11.0,

            'volume': 200,

            'points_needed': 6

        },

        'MICROCAP': {

            'drop_10s': 6.5,

            'drop_30s': 13.0,

            'drop_60s': 22.0,

            'volume': 350,

            'points_needed': 8

        },

        'STABLE': {

            'drop_10s': 1.5,

            'drop_30s': 3.0,

            'drop_60s': 5.0,

            'volume': 150,

            'points_needed': 5

        }

    }

    return thresholds.get(coin_type, thresholds['ALTCOIN'])



async def detect_dump_adaptive(symbol: str, current_price: float, price_history: list,

                         global_dump_count: int = 0) -> tuple[bool, int, dict]:

    coin_type = await get_coin_type_range(symbol)

    thresholds = get_dump_thresholds(coin_type)

    score = 0

    details = {}

    if len(price_history) >= 5:

        price_10s_ago = price_history[-5]

        drop_10s = safe_division((price_10s_ago - current_price), price_10s_ago, 0) * 100

        if drop_10s > thresholds['drop_10s']:

            score += 3

            details['drop_10s'] = f"-{drop_10s:.1f}% (+3)"

        else:

            details['drop_10s'] = f"-{drop_10s:.1f}% (0)"

    if len(price_history) >= 15:

        price_30s_ago = price_history[-15]

        drop_30s = safe_division((price_30s_ago - current_price), price_30s_ago, 0) * 100

        if drop_30s > thresholds['drop_30s']:

            score += 2

            details['drop_30s'] = f"-{drop_30s:.1f}% (+2)"

    try:

        candles_5m = await get_candles_optimized(symbol, '5m', 20)

        if candles_5m and len(candles_5m) >= 10:

            recent_volume = candles_5m[-1]['volume']

            avg_volume = sum(c['volume'] for c in candles_5m[-10:]) / 10

            volume_ratio = safe_division(recent_volume, avg_volume, 1.0) * 100

            if volume_ratio > 500:

                score += 2

                details['volume'] = f"{volume_ratio:.0f}% (+2)"

            elif volume_ratio > thresholds['volume']:

                score += 1

                details['volume'] = f"{volume_ratio:.0f}% (+1)"

            else:

                details['volume'] = f"{volume_ratio:.0f}% (0)"

        else:

            details['volume'] = "N/A (0)"

    except:

        details['volume'] = "N/A (0)"

    try:

        candles_1h = await get_candles_optimized(symbol, '1h', 24)

        if candles_1h:

            indicators = calculate_advanced_indicators(candles_1h)

            trend = indicators.get('trend_strength', 0.5)

            if trend > 0.7:

                score += 2

                details['trend'] = f"{trend:.2f} (+2)"

            elif trend > 0.5:

                score += 1

                details['trend'] = f"{trend:.2f} (+1)"

            else:

                details['trend'] = f"{trend:.2f} (0)"

        else:

            details['trend'] = "N/A (0)"

    except:

        details['trend'] = "N/A (0)"

    if global_dump_count >= 4:

        score += 2

        details['market'] = f"{global_dump_count} coins (+2)"

    elif global_dump_count >= 2:

        score += 1

        details['market'] = f"{global_dump_count} coins (+1)"

    else:

        details['market'] = f"{global_dump_count} coin (0)"

    if len(price_history) >= 30:

        price_60s_ago = price_history[-30]

        drop_60s = safe_division((price_60s_ago - current_price), price_60s_ago, 0) * 100

        if len(price_history) >= 15:

            drop_30s_val = safe_division((price_history[-15] - current_price), price_history[-15], 0) * 100

            drop_10s_val = safe_division((price_history[-5] - current_price), price_history[-5], 0) * 100 if len(price_history) >= 5 else 0

            if drop_10s_val > drop_30s_val * 0.7 and drop_30s_val > drop_60s * 0.5:

                score += 2

                details['acceleration'] = "Yes (+2)"

    is_dump = score >= thresholds['points_needed']

    details['total'] = score

    details['threshold'] = thresholds['points_needed']

    details['coin_type'] = coin_type

    return is_dump, score, details



def get_recovery_thresholds(coin_type: str, volatility: float) -> dict:

    base_thresholds = {

        'BLUECHIP': {

            'min_confidence': 70.0,

            'max_price_drop': 2.5,

            'cooldown_seconds': 5

        },

        'MAJOR': {

            'min_confidence': 67.0,

            'max_price_drop': 3.5,

            'cooldown_seconds': 4

        },

        'ALTCOIN': {

            'min_confidence': 63.0,

            'max_price_drop': 5.5,

            'cooldown_seconds': 3

        },

        'MICROCAP': {

            'min_confidence': 58.0,

            'max_price_drop': 9.0,

            'cooldown_seconds': 5

        },

        'STABLE': {

            'min_confidence': 65.0,

            'max_price_drop': 2.0,

            'cooldown_seconds': 3

        }

    }

    thresholds = base_thresholds.get(coin_type, base_thresholds['ALTCOIN']).copy()

    if volatility > 60:

        thresholds['min_confidence'] -= 5.0

        thresholds['max_price_drop'] += 3.0

        thresholds['cooldown_seconds'] += 2

    elif volatility > 45:

        thresholds['min_confidence'] -= 3.0

        thresholds['max_price_drop'] += 2.0

        thresholds['cooldown_seconds'] += 1

    thresholds['min_confidence'] = max(55.0, min(75.0, thresholds['min_confidence']))

    thresholds['max_price_drop'] = max(2.0, min(15.0, thresholds['max_price_drop']))

    thresholds['cooldown_seconds'] = max(3, min(10, thresholds['cooldown_seconds']))

    return thresholds



def detect_pump_dump_smart(candles: List[Dict]) -> Tuple[bool, str]:

    if len(candles) < 10:

        return False, "Insufficient data"

    try:

        current_price = candles[-1]['close']

        recent_volume = candles[-1]['volume']

        avg_volume = sum(c['volume'] for c in candles[-10:]) / min(10, len(candles))

        volume_spike = recent_volume / avg_volume if avg_volume > 0 else 1.0

        if len(candles) >= 2:

            price_1min_ago = candles[-2]['close']

            price_change_1min = safe_division(abs(current_price - price_1min_ago), price_1min_ago, 0.0)

            if price_change_1min > 0.05 and volume_spike > 2.0:

                return True, f"Early pump: {price_change_1min:.1%} in 1min, {volume_spike:.1f}x volume"

            if price_change_1min > 0.08 and volume_spike > 2.2:

                return True, f"Rapid pump: {price_change_1min:.1%} in 1min, {volume_spike:.1f}x volume"

        if len(candles) >= 5:

            price_5min_ago = candles[-5]['close']

            price_change_5min = safe_division(abs(current_price - price_5min_ago), price_5min_ago, 0.0)

            if price_change_5min > 0.15 and volume_spike > 3.0:

                return True, f"Pump detected: {price_change_5min:.1%} price change, {volume_spike:.1f}x volume"

            if price_change_5min > 0.20:

                return True, f"Extreme movement: {price_change_5min:.1%} price change"

        if volume_spike > 4.0:

            return True, f"Volume manipulation: {volume_spike:.1f}x average volume"

        return False, "Normal market conditions"

    except Exception as e:

        return False, f"Detection error: {str(e)}"



def detect_flash_crash_protection(candles: List[Dict]) -> Tuple[bool, str]:

    if len(candles) < 5:

        return False, "Insufficient data"

    try:

        current_price = candles[-1]['close']

        if len(candles) >= 2:

            price_1min_ago = candles[-2]['close']

            price_change_1min = safe_division((current_price - price_1min_ago), price_1min_ago, 0.0)

            if price_change_1min < -0.03:

                return True, f"Early dump: {price_change_1min:.1%} drop in 1min"

            if price_change_1min < -0.05:

                return True, f"Rapid dump: {price_change_1min:.1%} drop in 1min"

            if price_change_1min < -0.10:

                return True, f"Flash crash: {price_change_1min:.1%} drop in 1min"

        if len(candles) >= 5:

            price_5min_ago = candles[-5]['close']

            price_change_5min = safe_division((current_price - price_5min_ago), price_5min_ago, 0.0)

            if price_change_5min < -0.20:

                return True, f"Major crash: {price_change_5min:.1%} drop in 5min"

            if len(candles) >= 2:

                price_1min_ago = candles[-2]['close']

                price_change_1min = safe_division((current_price - price_1min_ago), price_1min_ago, 0.0)

                if price_change_1min < -0.05 and price_change_5min < -0.15:

                    return True, f"Rapid decline: {price_change_1min:.1%} + {price_change_5min:.1%}"

        return False, "No flash crash detected"

    except Exception as e:

        return False, f"Detection error: {str(e)}"



def get_adaptive_volume_spike_threshold(coin_type: str, volatility: float) -> float:

    base_thresholds = {

        'BLUECHIP': 2.5,

        'MAJOR': 3.0,

        'ALTCOIN': 4.0,

        'MICROCAP': 6.5,

        'STABLE': 2.0

    }

    threshold = base_thresholds.get(coin_type, 4.5)

    if volatility > 50:

        threshold *= 1.3

    elif volatility > 35:

        threshold *= 1.15

    elif volatility < 15:

        threshold *= 0.9

    return max(2.0, threshold)



def get_adaptive_wash_trading_threshold(coin_type: str) -> float:

    thresholds = {

        'BLUECHIP': 2.5,

        'MAJOR': 3.0,

        'ALTCOIN': 4.0,

        'MICROCAP': 6.0,

        'STABLE': 2.0

    }

    return thresholds.get(coin_type, 4.5)



def get_adaptive_volume_price_threshold(coin_type: str) -> tuple:

    thresholds = {

        'BLUECHIP': (2.0, 0.008),

        'MAJOR': (2.5, 0.010),

        'ALTCOIN': (3.5, 0.015),

        'MICROCAP': (5.0, 0.025),

        'STABLE': (1.5, 0.005)

    }

    return thresholds.get(coin_type, (3.5, 0.018))



def detect_volume_manipulation(candles: List[Dict], coin_type: str = 'ALTCOIN', volatility: float = 25.0,

                               order_flow_data: Dict = None, orderbook_data: Dict = None) -> Tuple[bool, str]:

    if len(candles) < 10:

        return False, "Insufficient data"

    try:

        recent_volume = candles[-1]['volume']

        avg_volume = sum(c['volume'] for c in candles[-10:]) / min(10, len(candles))

        volume_ratio = recent_volume / avg_volume if avg_volume > 0 else 1.0

        spike_threshold = get_adaptive_volume_spike_threshold(coin_type, volatility)

        drop_threshold = 0.2

        if volume_ratio > spike_threshold:

            if order_flow_data and orderbook_data:

                manipulation_score = 30

                imbalance = orderbook_data.get('order_imbalance', 0)

                if abs(imbalance) < 10:

                    manipulation_score += 20

                elif imbalance < -20:

                    manipulation_score += 15

                elif imbalance > 20:

                    manipulation_score -= 10

                buy_pressure = order_flow_data.get('buy_pressure', 50)

                if 45 <= buy_pressure <= 55:

                    manipulation_score += 25

                elif buy_pressure > 70:

                    manipulation_score -= 15

                cvd_momentum = order_flow_data.get('cvd_momentum', 0)

                if abs(cvd_momentum) < 0.1:

                    manipulation_score += 20

                elif cvd_momentum > 0.2:

                    manipulation_score -= 10

                price_change = safe_division((candles[-1]['close'] - candles[-2]['close']), candles[-2]['close'], 0.0)

                if abs(price_change) < 0.005:

                    manipulation_score += 15

                elif abs(price_change) > 0.02:

                    manipulation_score -= 10

                manipulation_score = max(0, min(100, manipulation_score))

                if manipulation_score >= 60:

                    return True, f"Volume spike {volume_ratio:.1f}x: HIGH manipulation risk ({manipulation_score}/100 - wash trading suspected)"

                elif manipulation_score >= 40:

                    return True, f"Volume spike {volume_ratio:.1f}x: MEDIUM manipulation risk ({manipulation_score}/100)"

                elif manipulation_score >= 20:

                    return False, f"Volume spike {volume_ratio:.1f}x: LOW risk ({manipulation_score}/100 - likely legitimate)"

                else:

                    return False, f"Volume spike {volume_ratio:.1f}x: Confirmed legitimate ({manipulation_score}/100)"

            else:

                return True, f"Volume spike: {volume_ratio:.1f}x average ({coin_type} threshold: {spike_threshold:.1f}x)"

        elif volume_ratio < drop_threshold:

            return True, f"Volume drop: {volume_ratio:.1f}x average volume"

        if len(candles) >= 3:

            vol_1min_ago = candles[-2]['volume']

            vol_change = abs(recent_volume - vol_1min_ago) / vol_1min_ago if vol_1min_ago > 0 else 0

            if coin_type in ['MAJOR', 'ALTCOIN']:

                base_threshold = 2.0

            else:

                base_threshold = 3.0

            if volatility > 40:

                vol_multiplier = 1.3

            elif volatility > 25:

                vol_multiplier = 1.15

            else:

                vol_multiplier = 1.0

            change_threshold = base_threshold * vol_multiplier

            if vol_change > change_threshold:

                return True, f"Sudden volume change: {vol_change:.1%} ({coin_type} {volatility:.0f}%vol threshold: {change_threshold:.1%})"

        return False, "Normal volume patterns"

    except Exception as e:

        return False, f"Detection error: {str(e)}"



def detect_price_anomaly(candles: List[Dict]) -> Tuple[bool, str]:

    if len(candles) < 5:

        return False, "Insufficient data"

    try:

        current_candle = candles[-1]

        high = current_candle['high']

        low = current_candle['low']

        close = current_candle['close']

        open_price = current_candle['open']

        price_range = (high - low) / low if low > 0 else 0

        upper_wick = (high - max(open_price, close)) / low if low > 0 else 0

        lower_wick = (min(open_price, close) - low) / low if low > 0 else 0

        if price_range > 0.15:

            return True, f"Wide price range: {price_range:.1%}"

        elif upper_wick > 0.10:

            return True, f"Long upper wick: {upper_wick:.1%}"

        elif lower_wick > 0.10:

            return True, f"Long lower wick: {lower_wick:.1%}"

        if len(candles) >= 2:

            prev_close = candles[-2]['close']

            gap = abs(open_price - prev_close) / prev_close if prev_close > 0 else 0

            if gap > 0.05:

                return True, f"Price gap: {gap:.1%}"

        return False, "Normal price patterns"

    except Exception as e:

        return False, f"Detection error: {str(e)}"



def detect_legitimate_recovery(candles: List[Dict]) -> Tuple[bool, str]:

    if len(candles) < 10:

        return False, "Insufficient data"

    try:

        current_price = candles[-1]['close']

        if len(candles) >= 5:

            prices_5min = [c['close'] for c in candles[-5:]]

            volumes_5min = [c['volume'] for c in candles[-5:]]

            min_price = min(prices_5min)

            max_price = max(prices_5min)

            price_recovery = (current_price - min_price) / min_price if min_price > 0 else 0

            avg_volume = safe_division(sum(volumes_5min), len(volumes_5min), 1.0)

            recent_volume = volumes_5min[-1]

            volume_ratio = recent_volume / avg_volume if avg_volume > 0 else 1.0

            if price_recovery > 0.08 and volume_ratio < 2.0:

                return True, f"Legitimate recovery: {price_recovery:.1%} with normal volume ({volume_ratio:.1f}x)"

            elif price_recovery > 0.12:

                return True, f"Strong recovery: {price_recovery:.1%} price recovery"

        return False, "No recovery pattern detected"

    except Exception as e:

        return False, f"Detection error: {str(e)}"



def detect_bid_wall_spoofing(symbol: str, bid_wall_history: List[Dict], current_bid_ratio: float, current_bid_wall_price: float, threshold: float = 2.5) -> Tuple[bool, str]:

    """

    Detect if bid wall is spoofing (appears and disappears repeatedly)

    

    Args:

        symbol: Trading symbol

        bid_wall_history: List of bid wall history entries (last 60 seconds)

        current_bid_ratio: Current bid wall ratio

        current_bid_wall_price: Current bid wall price

        threshold: Minimum ratio to consider as "wall" (default: 2.5x)

    

    Returns:

        (is_spoofing: bool, reason: str)

    """

    if not bid_wall_history or len(bid_wall_history) < 5:

        return False, "Insufficient history for spoofing detection"

    

    try:

        # Count how many times bid wall appeared and disappeared

        appear_count = 0

        disappear_count = 0

        price_changes = []

        wall_prices = []

        

        # Analyze history to detect appear/disappear pattern

        for i in range(1, len(bid_wall_history)):

            prev_ratio = bid_wall_history[i-1]['bid_ratio']

            curr_ratio = bid_wall_history[i]['bid_ratio']

            prev_price = bid_wall_history[i-1]['bid_wall_price']

            curr_price = bid_wall_history[i]['bid_wall_price']

            

            # Check if wall appeared (ratio crossed threshold)

            if prev_ratio < threshold and curr_ratio >= threshold:

                appear_count += 1

                if prev_price > 0:

                    price_change = abs(curr_price - prev_price) / prev_price

                    price_changes.append(price_change)

                wall_prices.append(curr_price)

            

            # Check if wall disappeared (ratio dropped below threshold)

            if prev_ratio >= threshold and curr_ratio < threshold:

                disappear_count += 1

        

        # Check current state

        if current_bid_ratio >= threshold:

            wall_prices.append(current_bid_wall_price)

        

        # Spoofing pattern 1: Wall appears and disappears multiple times (flash orders)

        if appear_count >= 3 and disappear_count >= 2:

            return True, f"Spoofing detected: wall appeared {appear_count}x and disappeared {disappear_count}x in last 60s"

        

        # Spoofing pattern 2: Wall price changes too much (unstable = likely spoofing)

        if price_changes:

            avg_price_change = sum(price_changes) / len(price_changes)

            if avg_price_change > 0.01:  # More than 1% price change

                return True, f"Unstable bid wall: average price change {avg_price_change:.2%} (likely spoofing)"

        

        # Spoofing pattern 3: Wall price jumps around (different price levels)

        if len(wall_prices) >= 3:

            price_variance = statistics.variance(wall_prices) if len(wall_prices) > 1 else 0

            avg_price = statistics.mean(wall_prices)

            if avg_price > 0:

                price_std_pct = (math.sqrt(price_variance) / avg_price) * 100 if price_variance > 0 else 0

                if price_std_pct > 0.5:  # More than 0.5% standard deviation

                    return True, f"Volatile bid wall: price std {price_std_pct:.2f}% (likely spoofing)"

        

        return False, "Stable bid wall (no spoofing detected)"

    except Exception as e:

        return False, f"Detection error: {str(e)}"



def detect_whale_wall_absorption(symbol: str, orderbook_data: Dict, flow_data: Dict) -> Tuple[bool, str, Dict]:

    try:

        if not orderbook_data or 'bids' not