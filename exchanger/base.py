"""
Base classes for exchange clients and WebSocket managers
"""
import time
import math
import asyncio
import requests
from typing import List, Dict, Optional, Any
from collections import deque
from collections import OrderedDict

# Import dependencies from core (will be available at runtime)
# These will be imported dynamically to avoid circular imports

class LRUCache:
    """LRU Cache implementation"""
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


class CircuitBreaker:
    """Circuit breaker for API calls"""
    def __init__(self, max_failures=5, reset_timeout=60):
        self.failures = 0
        self.max_failures = max_failures
        self.reset_timeout = reset_timeout
        self.last_failure_time = 0
        import threading
        self.lock = threading.Lock()

    def can_execute(self):
        with self.lock:
            if self.failures >= self.max_failures:
                if time.time() - self.last_failure_time > self.reset_timeout:
                    self.failures = 0
                    # Circuit breaker reset messages disabled to reduce clutter
                    # Import colors dynamically
                    # try:
                    #     from core import GREEN, RESET
                    #     print(f"{GREEN}✅ Circuit breaker reset{RESET}")
                    # except:
                    #     print("✅ Circuit breaker reset")
                    pass
                else:
                    return False
            return True

    def record_failure(self):
        with self.lock:
            self.failures += 1
            self.last_failure_time = time.time()
            try:
                from core import RED, RESET
                # Circuit breaker messages disabled to reduce clutter
                # print(f"{RED}🚨 Circuit breaker: {self.failures}/{self.max_failures} failures{RESET}")
            except:
                # print(f"🚨 Circuit breaker: {self.failures}/{self.max_failures} failures")
                pass

    def record_success(self):
        with self.lock:
            if self.failures > 0:
                self.failures = max(0, self.failures - 1)
                # Circuit breaker recovery messages disabled to reduce clutter
                # try:
                #     from core import GREEN, RESET
                #     print(f"{GREEN}✅ Circuit breaker: Recovery {self.failures}/{self.max_failures}{RESET}")
                # except:
                #     print(f"✅ Circuit breaker: Recovery {self.failures}/{self.max_failures}")
                pass


class BaseExchangeClient:
    """Base abstract class for all exchange clients"""
    def __init__(self, exchange_name: str):
        self.exchange_name = exchange_name
        self.session = requests.Session()
        try:
            from core import DISABLE_SSL_VERIFICATION, HAS_URLLIB3
            if DISABLE_SSL_VERIFICATION:
                self.session.verify = False
                if HAS_URLLIB3:
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except:
            pass
        self.cache = LRUCache(max_size=1000)
        self.request_timestamps = deque(maxlen=1200)
        self.request_count = 0
        self.circuit_breaker = CircuitBreaker(max_failures=3, reset_timeout=120)
        self.last_cache_cleanup = time.time()
        # Time-based cache for unsupported symbols (expires after 24 hours)
        # Format: {symbol: timestamp_when_added}
        self.unsupported_symbols = {}
        self.unsupported_symbols_ttl = 24 * 60 * 60  # 24 hours in seconds
        
    def _respect_rate_limits(self):
        """Override in subclass with exchange-specific rate limits"""
        if not self.circuit_breaker.can_execute():
            sleep_time = 30
            try:
                from core import RED, RESET
                print(f"{RED}🚨 CIRCUIT BREAKER ACTIVE - Waiting {sleep_time}s{RESET}")
            except:
                print(f"🚨 CIRCUIT BREAKER ACTIVE - Waiting {sleep_time}s")
            time.sleep(sleep_time)
            return
        now = time.time()
        recent_requests = [ts for ts in self.request_timestamps if now - ts < 60]
        current_rate = len(recent_requests)
        try:
            from core import TradingConfig, YELLOW, RESET
            max_allowed = int(TradingConfig.MAX_REQUESTS_PER_MINUTE * TradingConfig.RATE_LIMIT_BUFFER)
        except:
            max_allowed = 90
        if current_rate >= max_allowed:
            sleep_time = 60 - (now - recent_requests[0]) if recent_requests else 60
            sleep_time = max(0.1, min(60, sleep_time))
            try:
                from core import YELLOW, RESET
                print(f"{YELLOW}⚠️ Rate limit: {current_rate}/min - sleeping {sleep_time:.1f}s{RESET}")
            except:
                print(f"⚠️ Rate limit: {current_rate}/min - sleeping {sleep_time:.1f}s")
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
    
    def get_balance(self, currency: str = "USDT") -> Optional[float]:
        """Get account balance for a currency - must be implemented in subclass"""
        # Default implementation returns None (not implemented)
        # Subclasses can override this if they support balance checking
        return None
    
    def get_account_info(self) -> Optional[Dict]:
        """Get account information (balance, permissions, etc.) - must be implemented in subclass"""
        # Default implementation returns None (not implemented)
        # Subclasses can override this if they support account info
        return None
    
    def get_min_order_size(self, symbol: str) -> Optional[float]:
        """Get minimum order size for a symbol - must be implemented in subclass"""
        # Default implementation returns None (not implemented)
        # Subclasses can override this to fetch minimum order size from exchange
        return None
    
    def get_min_order_value(self, symbol: str) -> Optional[float]:
        """Get minimum order value (notional) for a symbol - must be implemented in subclass"""
        # Default implementation returns None (not implemented)
        # Subclasses can override this to fetch minimum order value from exchange
        return None
    
    def _format_cancel_orders_message(self, cancelled_count: int, coins: set = None) -> str:
        """Helper function to format cancel orders message consistently across all exchanges"""
        # Try to get dependencies dynamically
        try:
            # Try to import _get_deps from the exchange-specific module
            import sys
            module = sys.modules.get(self.__class__.__module__)
            if module and hasattr(module, '_get_deps'):
                deps = module._get_deps()
            else:
                # Fallback to direct import
                from core import YELLOW, GREEN, RESET
                deps = {'YELLOW': YELLOW, 'GREEN': GREEN, 'RESET': RESET}
        except:
            # Ultimate fallback
            deps = {'YELLOW': '\033[93m', 'GREEN': '\033[92m', 'RESET': '\033[0m'}
        
        coins = coins or set()
        
        if cancelled_count == 0:
            return f"{deps['YELLOW']}Tidak ada order untuk dibatalkan{deps['RESET']}"
        elif cancelled_count == 1:
            if coins:
                coin_list = ', '.join(sorted(coins))
                return f"{deps['GREEN']}1 order dibatalkan untuk: {coin_list}{deps['RESET']}"
            else:
                return f"{deps['GREEN']}1 order dibatalkan{deps['RESET']}"
        else:
            if coins:
                coin_list = ', '.join(sorted(coins))
                return f"{deps['GREEN']}{cancelled_count} order dibatalkan untuk: {coin_list}{deps['RESET']}"
            else:
                return f"{deps['GREEN']}{cancelled_count} order dibatalkan{deps['RESET']}"
    
    def cancel_all_spot_orders(self) -> bool:
        """Cancel all pending orders in spot trading - must be implemented in subclass"""
        # Default implementation returns False (not implemented)
        # Subclasses can override this to cancel all spot orders
        # They should use _format_cancel_orders_message() for consistent output
        return False
    
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
        # Also cleanup expired unsupported symbols
        self._cleanup_expired_unsupported_symbols()
    
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
        """Check if symbol is supported (cached with expiration)"""
        if symbol not in self.unsupported_symbols:
            return True
        
        # Check if cache entry has expired
        timestamp = self.unsupported_symbols.get(symbol)
        if timestamp is not None and isinstance(timestamp, (int, float)):
            if (time.time() - timestamp) > self.unsupported_symbols_ttl:
                # Cache expired - remove from unsupported list and retry
                del self.unsupported_symbols[symbol]
                return True
        
        return False
    
    def _add_unsupported_symbol(self, symbol: str):
        """Add symbol to unsupported list with timestamp"""
        self.unsupported_symbols[symbol] = time.time()
    
    def _cleanup_expired_unsupported_symbols(self):
        """Remove expired entries from unsupported_symbols cache"""
        current_time = time.time()
        expired_symbols = [
            sym for sym, ts in self.unsupported_symbols.items()
            if (current_time - ts) > self.unsupported_symbols_ttl
        ]
        for sym in expired_symbols:
            del self.unsupported_symbols[sym]
    
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
    
    def _is_invalid_symbol_error(self, error: Exception) -> bool:
        """Check if error is due to invalid/non-existent symbol - override in subclass if needed"""
        error_str = str(error).lower()
        
        # Check if it's a requests HTTPError with status codes that indicate invalid symbol
        if hasattr(error, 'response') and hasattr(error.response, 'status_code'):
            status_code = error.response.status_code
            # 400 Bad Request, 404 Not Found, 403 Forbidden (sometimes) can indicate invalid symbol
            if status_code in [400, 404]:
                # Try to get error message from response
                try:
                    import json
                    error_data = error.response.json()
                    error_msg = str(error_data.get('msg', '') or error_data.get('message', '') or '').lower()
                    invalid_keywords = ['invalid symbol', 'symbol', 'not found', 'does not exist', 'not supported', 'invalid']
                    if any(keyword in error_msg for keyword in invalid_keywords):
                        return True
                    # If 400/404 with no specific message, assume invalid symbol
                    return True
                except:
                    # If can't parse JSON, assume invalid symbol for 400/404
                    return True
            # 403 Forbidden might also indicate invalid symbol for some exchanges
            elif status_code == 403:
                # Check error message to see if it's about invalid symbol
                try:
                    import json
                    error_data = error.response.json()
                    error_msg = str(error_data.get('msg', '') or error_data.get('message', '') or '').lower()
                    invalid_keywords = ['invalid symbol', 'symbol', 'not found', 'does not exist', 'not supported']
                    if any(keyword in error_msg for keyword in invalid_keywords):
                        return True
                except:
                    pass  # Don't assume 403 is invalid symbol without checking message
        
        # Check error string for invalid symbol keywords
        invalid_keywords = ['400', '404', 'bad request', 'not found', 'invalid symbol', 'symbol not found', 'does not exist', 'not supported']
        if any(keyword in error_str for keyword in invalid_keywords):
            return True
        
        return False


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
            connections_copy = dict(self.connections)  # Copy to avoid modification during iteration
            for symbol, websocket in connections_copy.items():
                try:
                    if websocket and not websocket.closed:
                        await websocket.close()
                except asyncio.CancelledError:
                    raise  # Re-raise cancellation
                except Exception:
                    pass  # Ignore other errors during cleanup
            self.connections.clear()
        except asyncio.CancelledError:
            # If we're being cancelled, just clear connections
            self.connections.clear()
            raise
        except Exception as e:
            try:
                from core import RED, RESET
                print(f"{RED}❌ Error stopping WebSocket streams: {e}{RESET}")
            except:
                print(f"❌ Error stopping WebSocket streams: {e}")
            finally:
                # Always clear connections even on error
                self.connections.clear()
    
    def get_price(self, symbol: str) -> Optional[float]:
        """Get price from WebSocket cache"""
        if symbol in self.price_cache:
            return self.price_cache[symbol].get('price')
        return None
    
    def get_orderbook(self, symbol: str, limit: int = 100, max_age_s: float = 5.0) -> Optional[Dict]:
        """Get orderbook from WebSocket cache"""
        if symbol in self.orderbook_cache:
            ob_data = self.orderbook_cache[symbol]
            age = time.time() - ob_data.get('timestamp', 0)
            if age < max_age_s:
                return ob_data
        return None
    
    def get_cache_status(self, symbol: str) -> Dict[str, Any]:
        """Get WebSocket cache status for a symbol"""
        now = time.time()
        
        # Check price cache
        price_warm = False
        price_age_s = None
        if symbol in self.price_cache:
            price_data = self.price_cache[symbol]
            if isinstance(price_data, dict):
                price_age_s = now - price_data.get('timestamp', 0)
            else:
                # Legacy format: price_cache[symbol] = price (float)
                price_age_s = 0
            price_warm = price_age_s is not None and price_age_s < 10
        
        # Check orderbook cache
        ob_warm = False
        ob_age_s = None
        if symbol in self.orderbook_cache:
            ob_data = self.orderbook_cache[symbol]
            if isinstance(ob_data, dict):
                ob_age_s = now - ob_data.get('timestamp', 0)
            else:
                ob_age_s = 0
            ob_warm = ob_age_s is not None and ob_age_s < 10
        
        # Check candle cache
        intervals = ['1m', '5m', '15m', '1h', '4h']
        kline_status = {}
        warm_count = 0
        for iv in intervals:
            key = f"{symbol}_{iv}"
            warm = False
            if key in self.candle_cache:
                cache_data = self.candle_cache[key]
                if isinstance(cache_data, dict):
                    age = now - cache_data.get('timestamp', 0)
                    warm = age < 180 and bool(cache_data.get('candles'))
                else:
                    warm = False
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

