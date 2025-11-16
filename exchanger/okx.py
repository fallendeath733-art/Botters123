"""
OKX Exchange Client and WebSocket Manager
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
        okx_symbol = self.normalize_symbol(symbol)
        try:
            self.request_timestamps.append(time.time())
            url = f"{self.base_url}/api/v5/market/ticker"
            # OKX uses instType: SPOT, SWAP, FUTURES, OPTION
            inst_type = "SPOT" if category == "spot" else "SWAP" if category == "linear" else "SPOT"
            params = {
                "instId": okx_symbol,
                "instType": inst_type
            }
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
            else:
                # Check if error code indicates invalid symbol
                if data.get('code') != '0':
                    code = data.get('code', '')
                    msg = str(data.get('msg', '')).lower()
                    # OKX error codes for invalid symbol: typically 51000 or messages containing "invalid"
                    if code in ['51000', '51001'] or 'invalid' in msg or 'not found' in msg or 'does not exist' in msg:
                        if self.is_symbol_supported_cached(symbol):
                            print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di OKX Spot Market{deps['RESET']}")
                        self._add_unsupported_symbol(symbol)
                        return None
        except Exception as e:
            # Check if this is an invalid symbol error
            if self._is_invalid_symbol_error(e):
                if self.is_symbol_supported_cached(symbol):
                    print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di OKX Spot Market{deps['RESET']}")
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
                print(f"{deps['YELLOW']}⚠️ OKX API error for {symbol}: {str(e)[:100]}{deps['RESET']}")
            
            self.circuit_breaker.record_failure()
            # Only record non-SSL errors to error handler
            if not is_ssl_error and deps['error_handler']:
                deps['error_handler'].record_error("API_Error", f"Price fetch error: {symbol}", {"error": str(e)})
            return None
        print(f"{deps['RED']}❌ Failed to get price from OKX for {symbol}{deps['RESET']}")
        self.circuit_breaker.record_failure()
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
            
            # Check if error code indicates invalid symbol
            code = data.get('code', '')
            if code != '0':
                # OKX error codes for invalid symbol: typically 51000 or messages containing "invalid"
                msg = str(data.get('msg', '')).lower()
                if code in ['51000', '51001'] or 'invalid' in msg or 'not found' in msg or 'does not exist' in msg:
                    # Symbol not supported - don't trigger circuit breaker
                    if self.is_symbol_supported_cached(symbol):
                        print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di OKX Spot Market{deps['RESET']}")
                    self._add_unsupported_symbol(symbol)
                    return None
                # For other error codes, also don't trigger circuit breaker (might be API limitation)
                return None
            
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
            
            # No data returned but code is 0 - might be symbol not found
            if self.is_symbol_supported_cached(symbol):
                print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di OKX Spot Market{deps['RESET']}")
            self._add_unsupported_symbol(symbol)
            return None
        except Exception as e:
            # Check if this is an invalid symbol error
            if self._is_invalid_symbol_error(e):
                if self.is_symbol_supported_cached(symbol):
                    print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di OKX Spot Market{deps['RESET']}")
                self._add_unsupported_symbol(symbol)
                return None
            # For other errors, only print and record for critical issues
            error_str = str(e).lower()
            # Silent handling for SSL errors - they're often temporary
            is_ssl_error = 'ssl' in error_str or 'handshake' in error_str or 'sslv3' in error_str
            is_connection_error = 'connection' in error_str or 'timeout' in error_str or 'name resolution' in error_str or 'temporary failure' in error_str
            
            # Only log non-SSL errors
            if not is_ssl_error and not is_connection_error:
                print(f"{deps['YELLOW']}⚠️ OKX candle error for {symbol}: {str(e)[:100]}{deps['RESET']}")
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
            okx_symbol = self.normalize_symbol(symbol)
            url = f"{self.base_url}/api/v5/market/books"
            params = {
                'instId': okx_symbol,
                'sz': limit
            }
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Check for known API limitations
            code = data.get('code', '')
            if code != '0':
                # Don't trigger circuit breaker for known API limitations
                # Return None silently (orderbook is optional for analysis)
                return None
            
            if data.get('code') == '0' and data.get('data'):
                ob_data = data['data'][0]
                return {
                    'bids': [[float(bid[0]), float(bid[1])] for bid in ob_data.get('bids', [])],
                    'asks': [[float(ask[0]), float(ask[1])] for ask in ob_data.get('asks', [])],
                    'timestamp': int(time.time() * 1000)
                }
            return None
        except Exception as e:
            # Check if it's a known API limitation (400/404 errors)
            error_str = str(e).lower()
            if '400' in error_str or '404' in error_str or 'invalid' in error_str:
                # Don't print error or trigger circuit breaker for known API limitations
                # Orderbook endpoints may require authentication or have limitations
                return None
            # Only print and record for unexpected errors
            print(f"{deps['RED']}❌ OKX orderbook error: {e}{deps['RESET']}")
            self.circuit_breaker.record_failure()
            return None
    
    def get_aggregated_trades(self, symbol: str, limit: int = 500) -> Optional[List[Dict]]:
        deps = _get_deps()
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
            
            # Check for known API limitations
            code = data.get('code', '')
            if code != '0':
                # Don't trigger circuit breaker for known API limitations
                # Return None silently (trades are optional for analysis)
                return None
            
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
            # Check if it's a known API limitation (400/404 errors)
            error_str = str(e).lower()
            if '400' in error_str or '404' in error_str or 'invalid' in error_str:
                # Don't print error or trigger circuit breaker for known API limitations
                # Trades endpoints may require authentication or have limitations
                return None
            # Only print and record for unexpected errors
            print(f"{deps['RED']}❌ OKX aggregated trades error: {e}{deps['RESET']}")
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
            if not core.CONFIG or exchange_name not in core.CONFIG or exchange_name != 'OKX':
                return None
            
            api_key = core.CONFIG[exchange_name].get('API_KEY', '')
            api_secret = core.CONFIG[exchange_name].get('API_SECRET', '')
            passphrase = core.CONFIG[exchange_name].get('PASSPHRASE', '')
            testnet = core.CONFIG[exchange_name].get('TESTNET', 'false')
            
            placeholder_keys = ['your_okx_api_key_here', '']
            if not api_key or not api_secret or not passphrase or api_key in placeholder_keys:
                return None
            
            is_testnet = str(testnet).lower() == 'true'
            base_url = "https://www.okx.com"  # OKX uses same URL for testnet (different API key)
            
            # Get account balance using OKX API
            import hmac
            import hashlib
            import base64
            from time import time
            
            timestamp = str(time())
            method = 'GET'
            request_path = '/api/v5/account/balance'
            body = ''
            
            message = timestamp + method + request_path + body
            signature = base64.b64encode(
                hmac.new(
                    api_secret.encode('utf-8'),
                    message.encode('utf-8'),
                    hashlib.sha256
                ).digest()
            ).decode('utf-8')
            
            headers = {
                'OK-ACCESS-KEY': api_key,
                'OK-ACCESS-SIGN': signature,
                'OK-ACCESS-TIMESTAMP': timestamp,
                'OK-ACCESS-PASSPHRASE': passphrase,
                'Content-Type': 'application/json'
            }
            
            url = f"{base_url}{request_path}"
            response = self.session.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') == '0' and data.get('data'):
                account_data = data['data'][0]
                details = account_data.get('details', [])
                usdt_balance = 0.0
                
                for detail in details:
                    if detail.get('ccy') == 'USDT':
                        usdt_balance = float(detail.get('availBal', 0))
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


class OKXWebSocketManager(BaseWebSocketManager):
    """OKX WebSocket Manager"""
    def __init__(self):
        super().__init__("OKX")
        self.base_url = "wss://ws.okx.com:8443/ws/v5/public"
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
            okx_symbol = self.exchange_client.normalize_symbol(symbol) if hasattr(self, 'exchange_client') else symbol.replace('_', '-').upper()
            url = f"{self.base_url}"
            async with websockets.connect(url) as websocket:
                self.connections[symbol] = websocket
                subscribe_msg = {
                    "op": "subscribe",
                    "args": [
                        f"tickers/{okx_symbol}",
                        f"books5/{okx_symbol}",
                        f"trades/{okx_symbol}"
                    ]
                }
                await websocket.send(json.dumps(subscribe_msg))
                async for message in websocket:
                    try:
                        await self._process_message(symbol, json.loads(message))
                    except Exception as e:
                        print(f"{deps['YELLOW']}⚠️ WebSocket message error for {symbol}: {e}{deps['RESET']}")
        except websockets.exceptions.ConnectionClosed:
            # WebSocket connection closed messages disabled to reduce clutter
            # This is normal when connection drops, will auto-reconnect
            # print(f"{deps['YELLOW']}⚠️ WebSocket connection closed for {symbol}{deps['RESET']}")
            pass
        except Exception as e:
            print(f"{deps['RED']}❌ OKX WebSocket error: {e}{deps['RESET']}")
        finally:
            if symbol in self.connections:
                del self.connections[symbol]
    
    async def _process_message(self, symbol: str, data: dict):
        """Process WebSocket message"""
        deps = _get_deps()
        try:
            if 'event' in data:
                return  # Skip subscription confirmations
            if 'data' in data and len(data['data']) > 0:
                message_data = data['data'][0]
                arg = data.get('arg', {})
                channel = arg.get('channel', '')
                
                if 'ticker' in channel:
                    if 'last' in message_data:
                        price = float(message_data['last'])
                        if price > 0:
                            self.price_cache[symbol] = {
                                'price': price,
                                'timestamp': time.time()
                            }
                elif 'books' in channel:
                    if 'bids' in message_data and 'asks' in message_data:
                        self.orderbook_cache[symbol] = {
                            'bids': [[float(bid[0]), float(bid[1])] for bid in message_data['bids']],
                            'asks': [[float(ask[0]), float(ask[1])] for ask in message_data['asks']],
                            'timestamp': time.time()
                        }
                elif 'trades' in channel:
                    if symbol not in self.trade_cache:
                        self.trade_cache[symbol] = []
                    self.trade_cache[symbol].append({
                        'price': float(message_data.get('px', 0)),
                        'qty': float(message_data.get('sz', 0)),
                        'time': int(message_data.get('ts', time.time() * 1000)),
                        'is_buyer_maker': message_data.get('side') == 'sell'
                    })
                    if len(self.trade_cache[symbol]) > 100:
                        self.trade_cache[symbol] = self.trade_cache[symbol][-100:]
        except Exception as e:
            print(f"{deps['RED']}❌ WebSocket message processing error: {e}{deps['RESET']}")


