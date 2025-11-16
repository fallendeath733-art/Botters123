"""
KuCoin Exchange Client and WebSocket Manager
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


class KucoinClient(BaseExchangeClient):
    """KuCoin Exchange Client"""
    def __init__(self):
        super().__init__("KUCOIN")
        self.base_url = "https://api.kucoin.com"
        self.cache_ttl = {
            'price': 2,
            'candles_15m': 60,
            'candles_1h': 300,
            'candles_4h': 900
        }
    
    def normalize_symbol(self, symbol: str) -> str:
        """KuCoin uses BTC-USDT format"""
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
        
        kucoin_symbol = self.normalize_symbol(symbol)
        try:
            self.request_timestamps.append(time.time())
            # KuCoin uses different endpoints for spot and futures
            if category == "spot":
                url = f"{self.base_url}/api/v1/market/orderbook/level1"
            else:
                url = f"{self.base_url}/api/v1/futures/market/orderbook/level1"
            params = {"symbol": kucoin_symbol}
            r = self.session.get(url, params=params, timeout=5)
            r.raise_for_status()
            data = r.json()
            if data.get('code') == '200000' and data.get('data'):
                ticker = data['data']
                price = float(ticker.get('price', 0))
                if price > 0 and self.validate_price(price):
                    self.cache.set(cache_key, {'data': price, 'timestamp': time.time()})
                    self.circuit_breaker.record_success()
                    return price
            else:
                # Check if error code indicates invalid symbol
                code = data.get('code', '')
                msg = str(data.get('msg', '')).lower()
                # KuCoin error codes: 200000 is success, others might indicate invalid symbol
                if code != '200000':
                    if 'invalid' in msg or 'not found' in msg or 'does not exist' in msg or 'symbol' in msg:
                        if self.is_symbol_supported_cached(symbol):
                            print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di KuCoin Spot Market{deps['RESET']}")
                        self._add_unsupported_symbol(symbol)
                        return None
                # If code is 200000 but no data, might also be invalid
                if code == '200000' and not data.get('data'):
                    if self.is_symbol_supported_cached(symbol):
                        print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di KuCoin Spot Market{deps['RESET']}")
                    self._add_unsupported_symbol(symbol)
                    return None
        except Exception as e:
            # Check if this is an invalid symbol error
            if self._is_invalid_symbol_error(e):
                if self.is_symbol_supported_cached(symbol):
                    print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di KuCoin Spot Market{deps['RESET']}")
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
                print(f"{deps['YELLOW']}⚠️ KuCoin API error for {symbol}: {str(e)[:100]}{deps['RESET']}")
            
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
        
        kucoin_symbol = self.normalize_symbol(symbol)
        interval_map = {
            '1m': '1min', '3m': '3min', '5m': '5min', '15m': '15min', '30m': '30min',
            '1h': '1hour', '2h': '2hour', '4h': '4hour', '6h': '6hour', '12h': '12hour',
            '1d': '1day', '1w': '1week', '1M': '1month'
        }
        kucoin_interval = interval_map.get(interval, '5min')
        
        try:
            self.request_timestamps.append(time.time())
            url = f"{self.base_url}/api/v1/market/candles"
            params = {
                "symbol": kucoin_symbol,
                "type": kucoin_interval,
                "limit": min(limit, 1500)
            }
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            
            # Check if error code indicates invalid symbol
            code = data.get('code', '')
            if code != '200000':
                # KuCoin error codes for invalid symbol
                msg = str(data.get('msg', '')).lower()
                if 'invalid' in msg or 'not found' in msg or 'does not exist' in msg or 'not exist' in msg:
                    # Symbol not supported - don't trigger circuit breaker
                    if self.is_symbol_supported_cached(symbol):
                        print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di KuCoin Spot Market{deps['RESET']}")
                    self._add_unsupported_symbol(symbol)
                    return None
                # For other error codes, also don't trigger circuit breaker
                return None
            
            candles = []
            if data.get('code') == '200000' and data.get('data'):
                for k in reversed(data['data']):
                    try:
                        candle_data = {
                            "ts": float(k[0]),
                            "open": float(k[1]),
                            "close": float(k[2]),
                            "high": float(k[3]),
                            "low": float(k[4]),
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
            
            # No data returned but code is 200000 - might be symbol not found
            if self.is_symbol_supported_cached(symbol):
                print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di KuCoin Spot Market{deps['RESET']}")
            self._add_unsupported_symbol(symbol)
            return None
        except Exception as e:
            # Check if this is an invalid symbol error
            if self._is_invalid_symbol_error(e):
                if self.is_symbol_supported_cached(symbol):
                    print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di KuCoin Spot Market{deps['RESET']}")
                self._add_unsupported_symbol(symbol)
                return None
            # For other errors, only print and record for critical issues
            error_str = str(e).lower()
            # Silent handling for SSL errors - they're often temporary
            is_ssl_error = 'ssl' in error_str or 'handshake' in error_str or 'sslv3' in error_str
            is_connection_error = 'connection' in error_str or 'timeout' in error_str or 'name resolution' in error_str or 'temporary failure' in error_str
            
            # Only log non-SSL errors
            if not is_ssl_error and not is_connection_error:
                print(f"{deps['YELLOW']}⚠️ KuCoin candle error for {symbol}: {str(e)[:100]}{deps['RESET']}")
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
            kucoin_symbol = self.normalize_symbol(symbol)
            # KuCoin only supports specific limits: 20, 100
            # Map requested limit to nearest valid limit
            if limit <= 20:
                valid_limit = 20
            elif limit <= 100:
                valid_limit = 100
            else:
                valid_limit = 100  # Max supported
            
            url = f"{self.base_url}/api/v1/market/orderbook/level2_{valid_limit}"
            params = {'symbol': kucoin_symbol}
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            if data.get('code') == '200000' and data.get('data'):
                ob_data = data['data']
                bids = [[float(bid[0]), float(bid[1])] for bid in ob_data.get('bids', [])]
                asks = [[float(ask[0]), float(ask[1])] for ask in ob_data.get('asks', [])]
                
                # Trim to requested limit if needed
                if limit < len(bids):
                    bids = bids[:limit]
                if limit < len(asks):
                    asks = asks[:limit]
                
                return {
                    'bids': bids,
                    'asks': asks,
                    'timestamp': int(time.time() * 1000)
                }
        except Exception as e:
            # Don't print error for 404 if it's due to invalid limit (we'll try with valid limit)
            if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
                if e.response.status_code == 404:
                    # Try with valid limit (20 or 100)
                    try:
                        kucoin_symbol = self.normalize_symbol(symbol)
                        valid_limit = 20 if limit <= 20 else 100
                        url = f"{self.base_url}/api/v1/market/orderbook/level2_{valid_limit}"
                        params = {'symbol': kucoin_symbol}
                        response = self.session.get(url, params=params, timeout=10)
                        response.raise_for_status()
                        data = response.json()
                        if data.get('code') == '200000' and data.get('data'):
                            ob_data = data['data']
                            bids = [[float(bid[0]), float(bid[1])] for bid in ob_data.get('bids', [])]
                            asks = [[float(ask[0]), float(ask[1])] for ask in ob_data.get('asks', [])]
                            if limit < len(bids):
                                bids = bids[:limit]
                            if limit < len(asks):
                                asks = asks[:limit]
                            return {
                                'bids': bids,
                                'asks': asks,
                                'timestamp': int(time.time() * 1000)
                            }
                    except:
                        pass
            
            # Only print error if it's not a 404 (which we handle above)
            if not (hasattr(e, 'response') and hasattr(e.response, 'status_code') and e.response.status_code == 404):
                print(f"{deps['RED']}❌ KuCoin orderbook error: {e}{deps['RESET']}")
            self.circuit_breaker.record_failure()
            return None
        return None
    
    def get_aggregated_trades(self, symbol: str, limit: int = 500) -> Optional[List[Dict]]:
        deps = _get_deps()
        start_time = time.time()
        self._respect_rate_limits()
        self.request_count += 1
        try:
            kucoin_symbol = self.normalize_symbol(symbol)
            url = f"{self.base_url}/api/v1/market/histories"
            params = {
                'symbol': kucoin_symbol,
                'limit': min(limit, 500)
            }
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            processed_trades = []
            if data.get('code') == '200000' and data.get('data'):
                for trade in data['data']:
                    processed_trades.append({
                        'price': float(trade.get('price', 0)),
                        'qty': float(trade.get('size', 0)),
                        'time': int(trade.get('time', 0)),
                        'is_buyer_maker': trade.get('side') == 'sell'
                    })
            return processed_trades
        except Exception as e:
            print(f"{deps['RED']}❌ KuCoin aggregated trades error: {e}{deps['RESET']}")
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
            if not core.CONFIG or exchange_name not in core.CONFIG or exchange_name != 'KUCOIN':
                return None
            
            api_key = core.CONFIG[exchange_name].get('API_KEY', '')
            api_secret = core.CONFIG[exchange_name].get('API_SECRET', '')
            api_passphrase = core.CONFIG[exchange_name].get('API_PASSPHRASE', '')
            testnet = core.CONFIG[exchange_name].get('TESTNET', 'false')
            
            placeholder_keys = ['your_kucoin_api_key_here', '']
            if not api_key or not api_secret or not api_passphrase or api_key in placeholder_keys:
                return None
            
            is_testnet = str(testnet).lower() == 'true'
            base_url = "https://openapi-sandbox.kucoin.com" if is_testnet else "https://api.kucoin.com"
            
            # Get account balance using KuCoin API
            import hmac
            import hashlib
            import base64
            from time import time
            
            timestamp = str(int(time() * 1000))
            method = 'GET'
            endpoint = '/api/v1/accounts'
            query_string = 'currency=USDT&type=TRADE'
            
            str_to_sign = timestamp + method + endpoint + query_string
            signature = base64.b64encode(
                hmac.new(
                    api_secret.encode('utf-8'),
                    str_to_sign.encode('utf-8'),
                    hashlib.sha256
                ).digest()
            ).decode('utf-8')
            
            passphrase_signature = base64.b64encode(
                hmac.new(
                    api_secret.encode('utf-8'),
                    api_passphrase.encode('utf-8'),
                    hashlib.sha256
                ).digest()
            ).decode('utf-8')
            
            headers = {
                'KC-API-KEY': api_key,
                'KC-API-SIGN': signature,
                'KC-API-TIMESTAMP': timestamp,
                'KC-API-PASSPHRASE': passphrase_signature,
                'KC-API-KEY-VERSION': '2',
                'Content-Type': 'application/json'
            }
            
            url = f"{base_url}{endpoint}?{query_string}"
            response = self.session.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') == '200000' and data.get('data'):
                accounts = data['data']
                usdt_balance = 0.0
                
                for account in accounts:
                    if account.get('currency') == 'USDT' and account.get('type') == 'trade':
                        usdt_balance = float(account.get('available', 0))
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


class KucoinWebSocketManager(BaseWebSocketManager):
    """KuCoin WebSocket Manager"""
    def __init__(self):
        super().__init__("KUCOIN")
        self.base_url = None
        self.token = None
        self.token_expiry = 0
        self.cache_stats = {
            'price_hits': 0,
            'price_misses': 0,
            'orderbook_hits': 0,
            'orderbook_misses': 0,
            'flow_hits': 0,
            'flow_misses': 0,
            'last_report': time.time()
        }
    
    async def _get_connection_token(self):
        """Get KuCoin WebSocket connection token"""
        if self.token and time.time() < self.token_expiry:
            return self.base_url, self.token
        
        try:
            import requests
            token_url = "https://api.kucoin.com/api/v1/bullet-public"
            response = requests.post(token_url, timeout=10)
            token_data = response.json()
            if token_data.get('code') == '200000' and token_data.get('data'):
                self.token = token_data['data']['token']
                instance_server = token_data['data']['instanceServers'][0]
                self.base_url = instance_server['endpoint']
                # Token expires in 20 minutes, refresh 1 minute early
                self.token_expiry = time.time() + (instance_server.get('expireTime', 1200000) / 1000) - 60
                return self.base_url, self.token
        except Exception as e:
            print(f"Failed to get KuCoin token: {e}")
        return None, None
    
    async def start_stream(self, symbol: str):
        """Start WebSocket stream"""
        if symbol in self.connections:
            return
        deps = _get_deps()
        try:
            ws_url, token = await self._get_connection_token()
            if not ws_url or not token:
                print(f"{deps['RED']}❌ Failed to get KuCoin WebSocket token{deps['RESET']}")
                return
            
            ws_symbol = symbol.replace('_', '-').upper()
            full_url = f"{ws_url}?token={token}&connectId={int(time.time() * 1000)}"
            
            async with websockets.connect(full_url) as websocket:
                self.connections[symbol] = websocket
                
                # Subscribe to multiple channels
                subscribe_msgs = [
                    {
                        "id": int(time.time() * 1000),
                        "type": "subscribe",
                        "topic": f"/market/ticker:{ws_symbol}",
                        "privateChannel": False,
                        "response": True
                    },
                    {
                        "id": int(time.time() * 1000) + 1,
                        "type": "subscribe",
                        "topic": f"/market/level2:{ws_symbol}",
                        "privateChannel": False,
                        "response": True
                    },
                    {
                        "id": int(time.time() * 1000) + 2,
                        "type": "subscribe",
                        "topic": f"/market/match:{ws_symbol}",
                        "privateChannel": False,
                        "response": True
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
            print(f"{deps['RED']}❌ KuCoin WebSocket error: {e}{deps['RESET']}")
        finally:
            if symbol in self.connections:
                del self.connections[symbol]
    
    async def _process_message(self, symbol: str, data: dict):
        """Process WebSocket message"""
        deps = _get_deps()
        try:
            if 'type' in data:
                msg_type = data['type']
                if msg_type == 'welcome':
                    return
                elif msg_type == 'ack':
                    return
                elif msg_type == 'message':
                    topic = data.get('topic', '')
                    message_data = data.get('data', {})
                    
                    # Ticker
                    if '/market/ticker' in topic:
                        if isinstance(message_data, dict) and 'price' in message_data:
                            price = float(message_data['price'])
                            if price > 0:
                                self.price_cache[symbol] = {
                                    'price': price,
                                    'timestamp': time.time()
                                }
                    
                    # Orderbook (level2)
                    elif '/market/level2' in topic:
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
                    
                    # Trades
                    elif '/market/match' in topic:
                        if isinstance(message_data, dict):
                            if symbol not in self.trade_cache:
                                self.trade_cache[symbol] = []
                            trade = {
                                'price': float(message_data.get('price', 0)),
                                'qty': float(message_data.get('size', 0)),
                                'time': int(message_data.get('time', time.time() * 1000)),
                                'is_buyer_maker': message_data.get('side') == 'sell'
                            }
                            self.trade_cache[symbol].append(trade)
                            if len(self.trade_cache[symbol]) > 100:
                                self.trade_cache[symbol] = self.trade_cache[symbol][-100:]
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

