"""
Binance Exchange Client
"""
import time
from typing import List, Dict, Optional
from .base import BaseExchangeClient

# Import dependencies from core dynamically
def _get_deps():
    try:
        from core import (
            performance_monitor, error_handler,
            RED, YELLOW, RESET
        )
        return {
            'performance_monitor': performance_monitor,
            'error_handler': error_handler,
            'RED': RED, 'YELLOW': YELLOW, 'RESET': RESET
        }
    except ImportError:
        # Fallback if imports fail
        return {
            'performance_monitor': None,
            'error_handler': None,
            'RED': '', 'YELLOW': '', 'RESET': ''
        }


class BinanceClient(BaseExchangeClient):
    """Binance Exchange Client"""
    def __init__(self):
        super().__init__("BINANCE")
        self.base_url = "https://api.binance.com"
        self.cache_ttl = {
            'price': 2,
            'candles_15m': 60,
            'candles_1h': 300,
            'candles_4h': 900
        }
    
    def normalize_symbol(self, symbol: str) -> str:
        """Binance uses BTCUSDT format"""
        return symbol.replace('_', '').upper()
    
    def get_price_rest(self, symbol: str, category: str = "spot") -> Optional[float]:
        deps = _get_deps()
        performance_monitor = deps.get('performance_monitor')
        error_handler = deps.get('error_handler')
        RED = deps.get('RED', '')
        YELLOW = deps.get('YELLOW', '')
        RESET = deps.get('RESET', '')
        
        start_time = time.time()
        self._respect_rate_limits()
        if time.time() - self.last_cache_cleanup > 300:
            self.cleanup_old_cache()
        self.request_count += 1
        if performance_monitor:
            performance_monitor.record_api_call(time.time() - start_time)
        cache_key = f"price_{symbol}"
        cached_value = self.cache.get(cache_key)
        if cached_value and time.time() - cached_value['timestamp'] < self.cache_ttl['price']:
            return cached_value['data']
        binance_symbol = self.normalize_symbol(symbol)
        try:
            self.request_timestamps.append(time.time())
            # Binance uses different endpoints for spot and futures
            if category == "spot":
                url = f"{self.base_url}/api/v3/ticker/price"
            else:
                url = f"{self.base_url}/fapi/v1/ticker/price"
            params = {"symbol": binance_symbol}
            r = self.session.get(url, params=params, timeout=5)
            r.raise_for_status()
            data = r.json()
            if 'price' in data:
                price = float(data['price'])
                if self.validate_price(price):
                    self.cache.set(cache_key, {'data': price, 'timestamp': time.time()})
                    self.circuit_breaker.record_success()
                    return price
        except Exception as e:
            error_str = str(e).lower()
            # Silent handling for SSL errors - WebSocket fallback will handle price retrieval
            # SSL errors are often temporary and don't need to spam console
            is_ssl_error = 'ssl' in error_str or 'handshake' in error_str or 'sslv3' in error_str
            is_connection_error = 'connection' in error_str or 'timeout' in error_str or 'name resolution' in error_str or 'temporary failure' in error_str
            
            # Only log non-SSL/connection errors
            if not is_ssl_error and not is_connection_error:
                print(f"{YELLOW}⚠️ Binance API error for {symbol}: {str(e)[:100]}{RESET}")
            
            self.circuit_breaker.record_failure()
            # Only record non-SSL errors to error handler to avoid spam
            if not is_ssl_error and error_handler:
                error_handler.record_error("API_Error", f"Price fetch error: {symbol}", {"error": str(e)})
            return None
        print(f"{RED}❌ Failed to get price from Binance for {symbol}{RESET}")
        self.circuit_breaker.record_failure()
        return None
    
    def fetch_candles(self, symbol: str, interval: str = "5m", limit: int = 200) -> Optional[List[Dict]]:
        deps = _get_deps()
        performance_monitor = deps.get('performance_monitor')
        error_handler = deps.get('error_handler')
        RED = deps.get('RED', '')
        YELLOW = deps.get('YELLOW', '')
        RESET = deps.get('RESET', '')
        
        start_time = time.time()
        self._respect_rate_limits()
        if time.time() - self.last_cache_cleanup > 300:
            self.cleanup_old_cache()
        self.request_count += 1
        if performance_monitor:
            performance_monitor.record_api_call(time.time() - start_time)
        cache_key = f"candles_{symbol}_{interval}_{limit}"
        cached_value = self.cache.get(cache_key)
        if cached_value and time.time() - cached_value['timestamp'] < self.cache_ttl.get(f'candles_{interval}', 60):
            return cached_value['data']
        binance_symbol = self.normalize_symbol(symbol)
        interval_map = {
            '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
            '1h': '1h', '2h': '2h', '4h': '4h', '6h': '6h', '12h': '12h',
            '1d': '1d', '1w': '1w', '1M': '1M'
        }
        binance_interval = interval_map.get(interval, '5m')
        try:
            self.request_timestamps.append(time.time())
            url = f"{self.base_url}/api/v3/klines"
            params = {
                "symbol": binance_symbol,
                "interval": binance_interval,
                "limit": min(limit, 1000)
            }
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            candles = []
            for k in data:
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
            print(f"{YELLOW}⚠️ No candle data from Binance for {symbol}{RESET}")
            self.circuit_breaker.record_failure()
            return None
        except Exception as e:
            error_str = str(e).lower()
            # Silent handling for SSL errors - they're often temporary
            is_ssl_error = 'ssl' in error_str or 'handshake' in error_str or 'sslv3' in error_str
            is_connection_error = 'connection' in error_str or 'timeout' in error_str
            
            # Only log non-SSL errors
            if not is_ssl_error and not is_connection_error:
                print(f"{RED}❌ Binance candle error for {symbol}: {str(e)[:100]}{RESET}")
            
            self.circuit_breaker.record_failure()
            # Only record non-SSL errors to error handler
            if not is_ssl_error and error_handler:
                error_handler.record_error("API_Error", f"Candle fetch error: {symbol}", {"error": str(e)})
            return None
    
    def get_orderbook(self, symbol: str, limit: int = 100) -> Optional[Dict]:
        deps = _get_deps()
        RED = deps.get('RED', '')
        RESET = deps.get('RESET', '')
        
        start_time = time.time()
        self._respect_rate_limits()
        self.request_count += 1
        try:
            binance_symbol = self.normalize_symbol(symbol)
            url = f"{self.base_url}/api/v3/depth"
            params = {
                'symbol': binance_symbol,
                'limit': limit
            }
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if 'bids' in data and 'asks' in data:
                return {
                    'bids': [[float(bid[0]), float(bid[1])] for bid in data['bids']],
                    'asks': [[float(ask[0]), float(ask[1])] for ask in data['asks']],
                    'timestamp': int(time.time() * 1000)
                }
            return None
        except Exception as e:
            error_str = str(e).lower()
            # Silent handling for SSL/connection errors
            is_ssl_error = 'ssl' in error_str or 'handshake' in error_str or 'sslv3' in error_str
            is_connection_error = 'connection' in error_str or 'timeout' in error_str
            
            # Only log non-SSL errors
            if not is_ssl_error and not is_connection_error:
                print(f"{RED}❌ Binance orderbook error: {str(e)[:100]}{RESET}")
            
            self.circuit_breaker.record_failure()
            return None
    
    def get_aggregated_trades(self, symbol: str, limit: int = 500) -> Optional[List[Dict]]:
        deps = _get_deps()
        RED = deps.get('RED', '')
        RESET = deps.get('RESET', '')
        
        start_time = time.time()
        self._respect_rate_limits()
        self.request_count += 1
        try:
            binance_symbol = self.normalize_symbol(symbol)
            url = f"{self.base_url}/api/v3/trades"
            params = {
                'symbol': binance_symbol,
                'limit': min(limit, 1000)
            }
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            processed_trades = []
            for trade in data:
                processed_trades.append({
                    'price': float(trade['price']),
                    'qty': float(trade['qty']),
                    'time': int(trade['time']),
                    'is_buyer_maker': trade['isBuyerMaker']
                })
            return processed_trades
        except Exception as e:
            error_str = str(e).lower()
            # Silent handling for SSL/connection errors
            is_ssl_error = 'ssl' in error_str or 'handshake' in error_str or 'sslv3' in error_str
            is_connection_error = 'connection' in error_str or 'timeout' in error_str
            
            # Only log non-SSL errors
            if not is_ssl_error and not is_connection_error:
                print(f"{RED}❌ Binance aggregated trades error: {str(e)[:100]}{RESET}")
            
            self.circuit_breaker.record_failure()
            return None

# NOTE: Binance doesn't have a WebSocket manager implementation
# It uses BybitWebSocketManager as fallback (they use similar WebSocket protocols)
BinanceWebSocketManager = None

