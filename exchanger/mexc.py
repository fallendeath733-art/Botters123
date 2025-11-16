"""
MEXC Exchange Client and WebSocket Manager
"""
import asyncio
import time
import json
import os
from typing import List, Dict, Optional, Any
import websockets
from .base import BaseExchangeClient, BaseWebSocketManager

def _get_deps():
    try:
        from core import (
            performance_monitor, error_handler, safe_division,
            TradingConfig, output_manager,
            RED, GREEN, YELLOW, CYAN, RESET
        )
        return {
            'performance_monitor': performance_monitor,
            'error_handler': error_handler,
            'safe_division': safe_division,
            'TradingConfig': TradingConfig,
            'output_manager': output_manager,
            'RED': RED, 'GREEN': GREEN, 'YELLOW': YELLOW,
            'CYAN': CYAN, 'RESET': RESET
        }
    except ImportError:
        def safe_division(n, d, default=0.0):
            return (n / d) if d != 0 else default
        return {
            'performance_monitor': None,
            'error_handler': None,
            'safe_division': safe_division,
            'TradingConfig': type('obj', (object,), {'ENABLE_WS_CACHE_STATS': False}),
            'output_manager': None,
            'RED': '', 'GREEN': '', 'YELLOW': '',
            'CYAN': '', 'RESET': ''
        }


class MEXCClient(BaseExchangeClient):
    """MEXC Exchange Client"""
    def __init__(self):
        super().__init__("MEXC")
        self.base_url = "https://api.mexc.com"
        self.cache_ttl = {
            'price': 2,
            'candles_15m': 60,
            'candles_1h': 300,
            'candles_4h': 900
        }
    
    def normalize_symbol(self, symbol: str) -> str:
        """MEXC uses BTCUSDT format (no underscore)"""
        return symbol.replace('_', '').upper()
    
    def get_price_rest(self, symbol: str, category: str = "spot") -> Optional[float]:
        # Early return if symbol is already known to be unsupported
        if not self.is_symbol_supported_cached(symbol):
            return None
        
        deps = _get_deps()
        start_time = time.time()
        self._respect_rate_limits()
        if time.time() - self.last_cache_cleanup > 300:
            self.cleanup_old_cache()
        self.request_count += 1
        if deps['performance_monitor']:
            deps['performance_monitor'].record_api_call(time.time() - start_time)
        
        cache_key = f"price_{symbol}"
        cached_value = self.cache.get(cache_key)
        if cached_value and time.time() - cached_value['timestamp'] < self.cache_ttl['price']:
            return cached_value['data']
        
        mexc_symbol = self.normalize_symbol(symbol)
        try:
            self.request_timestamps.append(time.time())
            # MEXC uses different endpoints for spot and futures
            if category == "spot":
                url = f"{self.base_url}/api/v3/ticker/price"
                params = {"symbol": mexc_symbol}
            else:
                url = f"{self.base_url}/api/v1/contract/ticker/{mexc_symbol}"
                params = {}
            r = self.session.get(url, params=params, timeout=5)
            r.raise_for_status()
            data = r.json()
            
            if category == "spot":
                if 'price' in data:
                    price = float(data['price'])
                    if price > 0 and self.validate_price(price):
                        self.cache.set(cache_key, {'data': price, 'timestamp': time.time()})
                        self.circuit_breaker.record_success()
                        return price
                else:
                    # Symbol not found - no 'price' key in response
                    # MEXC returns data without 'price' key for invalid symbols
                    if self.is_symbol_supported_cached(symbol):
                        print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di MEXC Spot Market{deps['RESET']}")
                    self._add_unsupported_symbol(symbol)
                    return None
            else:
                # Futures: response structure is different
                if isinstance(data, dict) and 'data' in data:
                    ticker_data = data.get('data', {})
                    price = float(ticker_data.get('lastPrice', 0) or ticker_data.get('price', 0))
                    if price > 0 and self.validate_price(price):
                        self.cache.set(cache_key, {'data': price, 'timestamp': time.time()})
                        self.circuit_breaker.record_success()
                        return price
                elif 'price' in data:
                    # Fallback: try direct price field
                    price = float(data['price'])
                    if price > 0 and self.validate_price(price):
                        self.cache.set(cache_key, {'data': price, 'timestamp': time.time()})
                        self.circuit_breaker.record_success()
                        return price
                return None
        except Exception as e:
            # Check if this is an invalid symbol error
            # MEXC might return 403 for various reasons (rate limit, IP blocking, invalid symbol)
            # We need to be careful - 403 might not always mean invalid symbol
            if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
                if e.response.status_code == 403:
                    # For MEXC, 403 might mean invalid symbol OR rate limiting/IP blocking
                    # Check response body to see if it's about invalid symbol
                    try:
                        # Try to get response text
                        response_text = e.response.text.lower() if hasattr(e.response, 'text') else ''
                        # If response contains HTML "Access Denied", it's likely IP blocking, not invalid symbol
                        if 'access denied' in response_text or '<html>' in response_text:
                            # This is IP blocking/rate limiting, not invalid symbol
                            print(f"{deps['YELLOW']}⚠️ MEXC API error for {symbol}: {e}{deps['RESET']}")
                            self.circuit_breaker.record_failure()
                            return None
                        # Try to parse JSON error
                        error_data = e.response.json() if hasattr(e.response, 'json') else {}
                        error_msg = str(error_data.get('msg', '') or error_data.get('message', '') or '').lower()
                        # Only mark as invalid if message clearly indicates invalid symbol
                        if 'invalid' in error_msg or 'not found' in error_msg or 'symbol' in error_msg:
                            if self.is_symbol_supported_cached(symbol):
                                print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di MEXC Spot Market{deps['RESET']}")
                            self._add_unsupported_symbol(symbol)
                            return None
                        # If 403 with no clear message, assume it's rate limiting/IP blocking
                        # Don't print network/IP blocking errors to reduce clutter
                        # print(f"{deps['YELLOW']}⚠️ MEXC API error for {symbol}: {e}{deps['RESET']}")
                        self.circuit_breaker.record_failure()
                        return None
                    except:
                        # If can't parse, assume it's rate limiting/IP blocking, not invalid symbol
                        # Don't print network/IP blocking errors to reduce clutter
                        # print(f"{deps['YELLOW']}⚠️ MEXC API error for {symbol}: {e}{deps['RESET']}")
                        self.circuit_breaker.record_failure()
                        return None
            
            # For other errors (400, 404), check if it's invalid symbol
            if self._is_invalid_symbol_error(e):
                if self.is_symbol_supported_cached(symbol):
                    print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di MEXC Spot Market{deps['RESET']}")
                self._add_unsupported_symbol(symbol)
                return None
            # For other errors, record failure (but don't spam error messages)
            # Only print error for critical issues, not temporary network errors
            error_str = str(e).lower()
            # Silent handling for SSL errors - WebSocket fallback will handle price retrieval
            is_ssl_error = 'ssl' in error_str or 'handshake' in error_str or 'sslv3' in error_str
            is_connection_error = 'connection' in error_str or 'timeout' in error_str or 'name resolution' in error_str or 'temporary failure' in error_str
            
            # Only log non-SSL/connection errors
            if not is_ssl_error and not is_connection_error:
                print(f"{deps['YELLOW']}⚠️ MEXC API error for {symbol}: {str(e)[:100]}{deps['RESET']}")
            
            self.circuit_breaker.record_failure()
            # Only record non-SSL errors to error handler
            if not is_ssl_error and deps['error_handler']:
                deps['error_handler'].record_error("API_Error", f"Price fetch error: {symbol}", {"error": str(e)})
            return None
        return None
    
    def fetch_candles(self, symbol: str, interval: str = "5m", limit: int = 200) -> Optional[List[Dict]]:
        # Early return if symbol is already known to be unsupported
        if not self.is_symbol_supported_cached(symbol):
            return None
        
        deps = _get_deps()
        start_time = time.time()
        self._respect_rate_limits()
        if time.time() - self.last_cache_cleanup > 300:
            self.cleanup_old_cache()
        self.request_count += 1
        if deps['performance_monitor']:
            deps['performance_monitor'].record_api_call(time.time() - start_time)
        
        cache_key = f"candles_{symbol}_{interval}_{limit}"
        cached_value = self.cache.get(cache_key)
        if cached_value and time.time() - cached_value['timestamp'] < self.cache_ttl.get(f'candles_{interval}', 60):
            return cached_value['data']
        
        mexc_symbol = self.normalize_symbol(symbol)
        interval_map = {
            '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
            '1h': '1h', '4h': '4h', '1d': '1d', '1w': '1w'
        }
        mexc_interval = interval_map.get(interval, '5m')
        
        try:
            self.request_timestamps.append(time.time())
            url = f"{self.base_url}/api/v3/klines"
            params = {
                "symbol": mexc_symbol,
                "interval": mexc_interval,
                "limit": min(limit, 1000)
            }
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            
            # MEXC returns empty array [] if symbol not found, or error object
            if isinstance(data, dict) and data.get('code'):
                # Error response - check if invalid symbol
                msg = str(data.get('msg', '')).lower()
                if 'invalid' in msg or 'not found' in msg or 'does not exist' in msg:
                    # Symbol not supported - don't trigger circuit breaker
                    if self.is_symbol_supported_cached(symbol):
                        print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di MEXC Spot Market{deps['RESET']}")
                    self._add_unsupported_symbol(symbol)
                    return None
            
            candles = []
            if isinstance(data, list):
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
                # Empty list - symbol not found
                if self.is_symbol_supported_cached(symbol):
                    print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di MEXC Spot Market{deps['RESET']}")
                self._add_unsupported_symbol(symbol)
                return None
        except Exception as e:
            # Check if this is an invalid symbol error
            if self._is_invalid_symbol_error(e):
                if self.is_symbol_supported_cached(symbol):
                    print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di MEXC Spot Market{deps['RESET']}")
                self._add_unsupported_symbol(symbol)
                return None
            # For other errors, only print and record for critical issues
            error_str = str(e).lower()
            # Silent handling for SSL errors - they're often temporary
            is_ssl_error = 'ssl' in error_str or 'handshake' in error_str or 'sslv3' in error_str
            is_connection_error = 'connection' in error_str or 'timeout' in error_str or 'name resolution' in error_str or 'temporary failure' in error_str
            
            # Only log non-SSL errors
            if not is_ssl_error and not is_connection_error:
                print(f"{deps['YELLOW']}⚠️ MEXC candle error for {symbol}: {str(e)[:100]}{deps['RESET']}")
            # Don't trigger circuit breaker for network errors or known limitations
            if '400' not in error_str and '404' not in error_str:
                self.circuit_breaker.record_failure()
            # Only record non-SSL errors to error handler
            if not is_ssl_error and deps['error_handler']:
                deps['error_handler'].record_error("API_Error", f"Candle fetch error: {symbol}", {"error": str(e)})
            return None
    
    def get_orderbook(self, symbol: str, limit: int = 100) -> Optional[Dict]:
        deps = _get_deps()
        start_time = time.time()
        self._respect_rate_limits()
        self.request_count += 1
        try:
            mexc_symbol = self.normalize_symbol(symbol)
            url = f"{self.base_url}/api/v3/depth"
            params = {
                'symbol': mexc_symbol,
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
        except Exception as e:
            # Check if it's a known API limitation (400/404 errors)
            error_str = str(e).lower()
            if '400' in error_str or '404' in error_str or 'invalid' in error_str:
                # Don't print error or trigger circuit breaker for known API limitations
                # Orderbook endpoints may require authentication or have limitations
                return None
            # Only print and record for unexpected errors
            print(f"{deps['RED']}❌ MEXC orderbook error: {e}{deps['RESET']}")
            self.circuit_breaker.record_failure()
            return None
        return None
    
    def get_aggregated_trades(self, symbol: str, limit: int = 500) -> Optional[List[Dict]]:
        deps = _get_deps()
        start_time = time.time()
        self._respect_rate_limits()
        self.request_count += 1
        try:
            mexc_symbol = self.normalize_symbol(symbol)
            url = f"{self.base_url}/api/v3/trades"
            params = {
                'symbol': mexc_symbol,
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
            # Check if it's a known API limitation (400/404 errors)
            error_str = str(e).lower()
            if '400' in error_str or '404' in error_str or 'invalid' in error_str:
                # Don't print error or trigger circuit breaker for known API limitations
                # Trades endpoints may require authentication or have limitations
                return None
            # Only print and record for unexpected errors
            print(f"{deps['RED']}❌ MEXC aggregated trades error: {e}{deps['RESET']}")
            self.circuit_breaker.record_failure()
            return None
    
    def get_account_info(self) -> Optional[Dict]:
        """Get account information including balance"""
        deps = _get_deps()
        try:
            import core
            if not core.CONFIG:
                core.load_config()
            exchange_name = core.SELECTED_EXCHANGE
            if not core.CONFIG or exchange_name not in core.CONFIG or exchange_name != 'MEXC':
                return None
            
            api_key = core.CONFIG[exchange_name].get('API_KEY', '')
            api_secret = core.CONFIG[exchange_name].get('API_SECRET', '')
            testnet = core.CONFIG[exchange_name].get('TESTNET', 'false')
            
            placeholder_keys = ['your_mexc_api_key_here', '']
            if not api_key or not api_secret or api_key in placeholder_keys:
                return None
            
            is_testnet = str(testnet).lower() == 'true'
            base_url = "https://api.mexc.com"  # MEXC uses same URL
            
            # Get account balance using MEXC API
            import hmac
            import hashlib
            from time import time
            
            timestamp = str(int(time() * 1000))
            method = 'GET'
            path = '/api/v3/account'
            query_string = f'timestamp={timestamp}'
            
            signature = hmac.new(
                api_secret.encode('utf-8'),
                query_string.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            headers = {
                'X-MEXC-APIKEY': api_key,
                'Content-Type': 'application/json'
            }
            
            url = f"{base_url}{path}?{query_string}&signature={signature}"
            response = self.session.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if 'balances' in data:
                usdt_balance = 0.0
                for balance in data['balances']:
                    if balance.get('asset') == 'USDT':
                        usdt_balance = float(balance.get('free', 0))
                        break
                
                return {
                    'usdt_balance': usdt_balance,
                    'total_equity': usdt_balance,
                    'testnet': is_testnet,
                    'account_type': 'SPOT',
                    'status': 'connected'
                }
            
            return None
        except Exception:
            return None


class MEXCWebSocketManager(BaseWebSocketManager):
    """MEXC WebSocket Manager"""
    def __init__(self):
        super().__init__("MEXC")
        self.base_url = "wss://wbs.mexc.com/ws"
        self.cache_stats = {
            'price_hits': 0,
            'price_misses': 0,
            'orderbook_hits': 0,
            'orderbook_misses': 0,
            'flow_hits': 0,
            'flow_misses': 0,
            'last_report': time.time()
        }
    
    async def start_stream(self, symbol: str):
        """Start WebSocket stream"""
        if symbol in self.connections:
            return
        deps = _get_deps()
        try:
            ws_symbol = symbol.replace('_', '').upper()
            url = f"{self.base_url}"
            async with websockets.connect(url) as websocket:
                self.connections[symbol] = websocket
                
                # Subscribe to multiple channels
                subscribe_msgs = [
                    {
                        "method": "SUBSCRIPTION",
                        "params": [f"spot@public.deals.v3.api@{ws_symbol}"]  # Trades
                    },
                    {
                        "method": "SUBSCRIPTION",
                        "params": [f"spot@public.increase.depth.v3.api@{ws_symbol}"]  # Orderbook
                    },
                    {
                        "method": "SUBSCRIPTION",
                        "params": [f"spot@public.deal.v3.api@{ws_symbol}"]  # Ticker/Price
                    }
                ]
                
                for msg in subscribe_msgs:
                    await websocket.send(json.dumps(msg))
                    await asyncio.sleep(0.1)
                
                async for message in websocket:
                    try:
                        await self._process_message(symbol, json.loads(message))
                    except Exception as e:
                        print(f"{deps['YELLOW']}⚠️ WebSocket message error: {e}{deps['RESET']}")
        except websockets.exceptions.ConnectionClosed:
            # WebSocket connection closed messages disabled to reduce clutter
            # This is normal when connection drops, will auto-reconnect
            # print(f"{deps['YELLOW']}⚠️ WebSocket connection closed for {symbol}{deps['RESET']}")
            pass
        except Exception as e:
            print(f"{deps['RED']}❌ MEXC WebSocket error: {e}{deps['RESET']}")
        finally:
            if symbol in self.connections:
                del self.connections[symbol]
    
    async def _process_message(self, symbol: str, data: dict):
        """Process WebSocket message"""
        deps = _get_deps()
        try:
            # MEXC WebSocket format: {"c": "spot@public.deals.v3.api@BTCUSDT", "d": {...}}
            if 'c' in data and 'd' in data:
                channel = data['c']
                message_data = data['d']
                
                # Trades
                if 'deals.v3' in channel or 'deal.v3' in channel:
                    if isinstance(message_data, list):
                        for trade in message_data:
                            if symbol not in self.trade_cache:
                                self.trade_cache[symbol] = []
                            trade_data = {
                                'price': float(trade.get('p', 0)),
                                'qty': float(trade.get('v', 0)),
                                'time': int(trade.get('t', time.time() * 1000)),
                                'is_buyer_maker': trade.get('S') == 2  # 2 = sell, 1 = buy
                            }
                            self.trade_cache[symbol].append(trade_data)
                            if len(self.trade_cache[symbol]) > 100:
                                self.trade_cache[symbol] = self.trade_cache[symbol][-100:]
                            
                            # Also update price from trade
                            if trade_data['price'] > 0:
                                self.price_cache[symbol] = {
                                    'price': trade_data['price'],
                                    'timestamp': time.time()
                                }
                    elif isinstance(message_data, dict):
                        # Single trade
                        price = float(message_data.get('p', 0))
                        if price > 0:
                            self.price_cache[symbol] = {
                                'price': price,
                                'timestamp': time.time()
                            }
                
                # Orderbook depth
                elif 'depth' in channel:
                    if isinstance(message_data, dict):
                        if 'bids' in message_data and 'asks' in message_data:
                            try:
                                max_levels = int(os.getenv('ORDERBOOK_CACHE_LEVELS', '20'))
                            except Exception:
                                max_levels = 20
                            self.orderbook_cache[symbol] = {
                                'bids': [[float(bid[0]), float(bid[1])] for bid in message_data['bids'][:max_levels]],
                                'asks': [[float(ask[0]), float(ask[1])] for ask in message_data['asks'][:max_levels]],
                                'timestamp': time.time()
                            }
        except Exception as e:
            print(f"{deps['RED']}❌ WebSocket message processing error: {e}{deps['RESET']}")
    
    def get_price(self, symbol: str) -> Optional[float]:
        """Get price from WebSocket cache"""
        if symbol in self.price_cache:
            cache_data = self.price_cache[symbol]
            if time.time() - cache_data['timestamp'] < 30:
                self.cache_stats['price_hits'] += 1
                return cache_data['price']
        self.cache_stats['price_misses'] += 1
        return None
    
    def get_orderbook(self, symbol: str, limit: int = 20, max_age_s: int = 30) -> Optional[Dict]:
        """Get orderbook from WebSocket cache"""
        if symbol in self.orderbook_cache:
            cache_data = self.orderbook_cache[symbol]
            age = time.time() - cache_data.get('timestamp', 0)
            if age < max_age_s:
                self.cache_stats['orderbook_hits'] += 1
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
        return None

