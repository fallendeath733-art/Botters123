"""
Cache management for candles and market data.
"""
import asyncio
import time
import json
import os
from typing import Dict, List, Optional, Any
from collections import deque
from config.constants import (
    COIN_MARKET_DATA_FILE, CMC_CACHE_DEBUG, PERSIST_CANDLES,
    CANDLE_CACHE_DIR, CANDLE_AUTO_FLUSH_SEC, CANDLE_MAX_AGE,
    _cmc_request_timestamps, _cmc_rate_lock,
)
from config.colors import YELLOW, RESET


def _get_cmc_rate_lock():
    """Get or create CMC rate limit lock."""
    global _cmc_rate_lock
    if _cmc_rate_lock is None:
        _cmc_rate_lock = asyncio.Lock()
    return _cmc_rate_lock


async def cmc_rate_limit_guard():
    """Guard function to respect CoinMarketCap rate limits."""
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


def load_coin_market_cache() -> Dict[str, Any]:
    """Load coin market data cache from file."""
    if not os.path.exists(COIN_MARKET_DATA_FILE):
        return {}
    try:
        with open(COIN_MARKET_DATA_FILE, "r") as f:
            data = json.load(f)
        return data
    except Exception:
        return {}


def save_coin_market_cache(data: Dict[str, Any]):
    """Save coin market data cache to file."""
    with open(COIN_MARKET_DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_persisted_candles(symbol: str, interval: str, limit: int, max_age_s: int) -> Optional[List[Dict]]:
    """Load persisted candles from cache file."""
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
    """Save candles to cache file."""
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
    """Periodically flush candle cache to disk."""
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
            # Import here to avoid circular dependency
            from core import _init_ws_manager
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
    """Get coin market data with caching."""
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


__all__ = [
    'load_coin_market_cache', 'save_coin_market_cache',
    'load_persisted_candles', 'save_persisted_candles',
    'periodic_candle_cache_flush', 'get_coin_market_data',
    '_get_cmc_rate_lock', 'cmc_rate_limit_guard',
]

