"""
Bitget Exchange Client and WebSocket Manager
"""
import asyncio
import time
import json
import os
from typing import List, Dict, Optional, Any
import websockets
from .base import BaseExchangeClient, BaseWebSocketManager

# Import dependencies from core dynamically
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


class BitgetClient(BaseExchangeClient):
    """Bitget Exchange Client"""
    def __init__(self):
        super().__init__("BITGET")
        self.base_url = "https://api.bitget.com"
        self.cache_ttl = {
            'price': 2,
            'candles_15m': 60,
            'candles_1h': 300,
            'candles_4h': 900
        }
    
    def normalize_symbol(self, symbol: str) -> str:
        """Bitget uses BTCUSDT format"""
        return symbol.replace('_', '').upper()
    
    def get_price_rest(self, symbol: str, category: str = "spot") -> Optional[float]:
        """Get current price via REST API"""
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
        
        bitget_symbol = self.normalize_symbol(symbol)
        try:
            self.request_timestamps.append(time.time())
            # Bitget uses different endpoints for spot and futures
            if category == "spot":
                # Use /tickers endpoint (plural) as /ticker (singular) doesn't work with symbol parameter
                url = f"{self.base_url}/api/spot/v1/market/tickers"
            else:
                # Futures endpoint
                url = f"{self.base_url}/api/mix/v1/market/ticker"
            
            params = {}
            if category != "spot":
                params["productType"] = "umcbl"  # USDT-M perpetual
                params["symbol"] = bitget_symbol
            
            r = self.session.get(url, params=params, timeout=5)
            r.raise_for_status()
            data = r.json()
            
            if data.get('code') == '00000' and data.get('data'):
                if category == "spot":
                    # Filter tickers for our symbol
                    tickers = data.get('data', [])
                    ticker = None
                    for t in tickers:
                        if t.get('symbol') == bitget_symbol:
                            ticker = t
                            break
                    
                    if ticker:
                        price = float(ticker.get('close', 0))
                        if price > 0 and self.validate_price(price):
                            self.cache.set(cache_key, {'data': price, 'timestamp': time.time()})
                            self.circuit_breaker.record_success()
                            return price
                    else:
                        # Symbol not found in tickers list
                        if self.is_symbol_supported_cached(symbol):
                            print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di Bitget Spot Market{deps['RESET']}")
                        self._add_unsupported_symbol(symbol)
                        return None
                else:
                    # Futures: data is a single ticker object
                    ticker = data.get('data', {})
                    if ticker:
                        price = float(ticker.get('last', 0) or ticker.get('close', 0))
                        if price > 0 and self.validate_price(price):
                            self.cache.set(cache_key, {'data': price, 'timestamp': time.time()})
                            self.circuit_breaker.record_success()
                            return price
                    return None
            else:
                # API error
                code = data.get('code', '')
                msg = str(data.get('msg', '')).lower()
                if code != '00000':
                    # For API errors, don't mark symbol as unsupported (might be temporary)
                    # Only mark if it's clearly a symbol-related error
                    if 'symbol' in msg or 'not found' in msg:
                        if self.is_symbol_supported_cached(symbol):
                            print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di Bitget Spot Market{deps['RESET']}")
                        self._add_unsupported_symbol(symbol)
                    return None
        except Exception as e:
            # Check if this is an invalid symbol error
            if self._is_invalid_symbol_error(e):
                if self.is_symbol_supported_cached(symbol):
                    print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di Bitget Spot Market{deps['RESET']}")
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
                print(f"{deps['YELLOW']}⚠️ Bitget API error for {symbol}: {str(e)[:100]}{deps['RESET']}")
            
            self.circuit_breaker.record_failure()
            # Only record non-SSL errors to error handler
            if not is_ssl_error and deps['error_handler']:
                deps['error_handler'].record_error("API_Error", f"Price fetch error: {symbol}", {"error": str(e)})
            return None
        return None
    
    def fetch_candles(self, symbol: str, interval: str = "5m", limit: int = 200) -> Optional[List[Dict]]:
        """Fetch candle data"""
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
        
        bitget_symbol = self.normalize_symbol(symbol)
        interval_map = {
            '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
            '1h': '1H', '4h': '4H', '1d': '1D', '1w': '1W'
        }
        bitget_interval = interval_map.get(interval, '5m')
        
        try:
            self.request_timestamps.append(time.time())
            url = f"{self.base_url}/api/spot/v1/market/candles"
            params = {
                "symbol": bitget_symbol,
                "period": bitget_interval,
                "limit": min(limit, 1000)
            }
            r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            
            # Check if error code indicates invalid symbol
            code = data.get('code', '')
            if code != '00000':
                # Bitget error code 40034 typically means "Parameter does not exist"
                if code == '40034' or 'does not exist' in str(data.get('msg', '')).lower():
                    # Symbol not supported - don't trigger circuit breaker
                    if self.is_symbol_supported_cached(symbol):
                        print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di Bitget Spot Market{deps['RESET']}")
                    self._add_unsupported_symbol(symbol)
                    return None
                # For other error codes, also don't trigger circuit breaker
                return None
            
            candles = []
            if data.get('code') == '00000' and data.get('data'):
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
            
            # No data returned but code is 00000 - might be symbol not found
            if self.is_symbol_supported_cached(symbol):
                print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di Bitget Spot Market{deps['RESET']}")
            self._add_unsupported_symbol(symbol)
            return None
        except Exception as e:
            # Check if this is an invalid symbol error
            if self._is_invalid_symbol_error(e):
                if self.is_symbol_supported_cached(symbol):
                    print(f"{deps['YELLOW']}⚠️ {symbol} tidak tersedia di Bitget Spot Market{deps['RESET']}")
                self._add_unsupported_symbol(symbol)
                return None
            # For other errors, only print and record for critical issues
            error_str = str(e).lower()
            # Silent handling for SSL errors - they're often temporary
            is_ssl_error = 'ssl' in error_str or 'handshake' in error_str or 'sslv3' in error_str
            is_connection_error = 'connection' in error_str or 'timeout' in error_str or 'name resolution' in error_str or 'temporary failure' in error_str
            
            # Only log non-SSL errors
            if not is_ssl_error and not is_connection_error:
                print(f"{deps['YELLOW']}⚠️ Bitget candle error for {symbol}: {str(e)[:100]}{deps['RESET']}")
            # Don't trigger circuit breaker for network errors or known limitations
            if '400' not in error_str and '404' not in error_str:
                self.circuit_breaker.record_failure()
            # Only record non-SSL errors to error handler
            if not is_ssl_error and deps['error_handler']:
                deps['error_handler'].record_error("API_Error", f"Candle fetch error: {symbol}", {"error": str(e)})
            return None
    
    def get_orderbook(self, symbol: str, limit: int = 100) -> Optional[Dict]:
        """Get orderbook data"""
        deps = _get_deps()
        start_time = time.time()
        self._respect_rate_limits()
        self.request_count += 1
        try:
            bitget_symbol = self.normalize_symbol(symbol)
            url = f"{self.base_url}/api/spot/v1/market/depth"
            params = {
                'symbol': bitget_symbol,
                'limit': limit
            }
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Bitget API often returns 40034 for depth endpoint even with valid symbols
            # This might require authentication or use different endpoint
            code = data.get('code', '')
            if code == '40034' or code == '400':
                # Don't trigger circuit breaker for known API limitations
                # Return None silently (orderbook is optional for analysis)
                return None
            
            if data.get('code') == '00000' and data.get('data'):
                ob_data = data['data']
                return {
                    'bids': [[float(bid[0]), float(bid[1])] for bid in ob_data.get('bids', [])],
                    'asks': [[float(ask[0]), float(ask[1])] for ask in ob_data.get('asks', [])],
                    'timestamp': int(time.time() * 1000)
                }
        except Exception as e:
            # Check if it's a known Bitget API limitation (400/40034 errors)
            error_str = str(e).lower()
            if '400' in error_str or '40034' in error_str:
                # Don't print error or trigger circuit breaker for known API limitations
                # Bitget depth/fills endpoints may require authentication
                return None
            # Only print and record for unexpected errors
            print(f"{deps['RED']}❌ Bitget orderbook error: {e}{deps['RESET']}")
            self.circuit_breaker.record_failure()
            return None
        return None
    
    def get_aggregated_trades(self, symbol: str, limit: int = 500) -> Optional[List[Dict]]:
        """Get recent trades"""
        deps = _get_deps()
        start_time = time.time()
        self._respect_rate_limits()
        self.request_count += 1
        try:
            bitget_symbol = self.normalize_symbol(symbol)
            url = f"{self.base_url}/api/spot/v1/market/fills"
            params = {
                'symbol': bitget_symbol,
                'limit': min(limit, 100)
            }
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Bitget API often returns 40034 for fills endpoint even with valid symbols
            # This might require authentication or use different endpoint
            code = data.get('code', '')
            if code == '40034' or code == '400':
                # Don't trigger circuit breaker for known API limitations
                # Return None silently (trades are optional for analysis)
                return None
            
            processed_trades = []
            if data.get('code') == '00000' and data.get('data'):
                for trade in data['data']:
                    processed_trades.append({
                        'price': float(trade.get('price', 0)),
                        'qty': float(trade.get('size', 0)),
                        'time': int(trade.get('ts', 0)),
                        'is_buyer_maker': trade.get('side') == 'sell'
                    })
            return processed_trades
        except Exception as e:
            # Check if it's a known Bitget API limitation (400/40034 errors)
            error_str = str(e).lower()
            if '400' in error_str or '40034' in error_str:
                # Don't print error or trigger circuit breaker for known API limitations
                # Bitget depth/fills endpoints may require authentication
                return None
            # Only print and record for unexpected errors
            print(f"{deps['RED']}❌ Bitget aggregated trades error: {e}{deps['RESET']}")
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
            if not core.CONFIG or exchange_name not in core.CONFIG or exchange_name != 'BITGET':
                return None
            
            api_key = core.CONFIG[exchange_name].get('API_KEY', '')
            api_secret = core.CONFIG[exchange_name].get('API_SECRET', '')
            passphrase = core.CONFIG[exchange_name].get('PASSPHRASE', '')
            testnet = core.CONFIG[exchange_name].get('TESTNET', 'false')
            
            placeholder_keys = ['your_bitget_api_key_here', '']
            if not api_key or not api_secret or not passphrase or api_key in placeholder_keys:
                return None
            
            is_testnet = str(testnet).lower() == 'true'
            base_url = "https://api.bitget.com"  # Bitget uses same URL
            
            # Get account balance using Bitget API
            import hmac
            import hashlib
            import base64
            from time import time
            
            timestamp = str(int(time() * 1000))
            method = 'GET'
            request_path = '/api/spot/v1/account/assets'
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
                'ACCESS-KEY': api_key,
                'ACCESS-SIGN': signature,
                'ACCESS-TIMESTAMP': timestamp,
                'ACCESS-PASSPHRASE': passphrase,
                'Content-Type': 'application/json'
            }
            
            url = f"{base_url}{request_path}"
            response = self.session.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get('code') == '00000' and data.get('data'):
                assets = data['data']
                usdt_balance = 0.0
                
                for asset in assets:
                    if asset.get('coinName') == 'USDT':
                        usdt_balance = float(asset.get('available', 0))
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


class BitgetWebSocketManager(BaseWebSocketManager):
    """Bitget WebSocket Manager"""
    def __init__(self):
        super().__init__("BITGET")
        self.base_url = "wss://ws.bitget.com/spot/v1/stream"
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
                        "op": "subscribe",
                        "args": [f"ticker/{ws_symbol}"]
                    },
                    {
                        "op": "subscribe",
                        "args": [f"depth/{ws_symbol}"]
                    },
                    {
                        "op": "subscribe",
                        "args": [f"trade/{ws_symbol}"]
                    }
                ]
                
                for msg in subscribe_msgs:
                    await websocket.send(json.dumps(msg))
                    await asyncio.sleep(0.1)
                
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
            print(f"{deps['RED']}❌ Bitget WebSocket error: {e}{deps['RESET']}")
        finally:
            if symbol in self.connections:
                del self.connections[symbol]
    
    async def _process_message(self, symbol: str, data: dict):
        """Process WebSocket message"""
        deps = _get_deps()
        try:
            if 'action' in data and data['action'] == 'subscribe':
                return
            if 'data' in data:
                message_data = data['data']
                arg = str(data.get('arg', ''))
                
                if 'ticker' in arg:
                    # Process ticker update
                    if isinstance(message_data, dict) and 'last' in message_data:
                        price = float(message_data['last'])
                        if price > 0:
                            self.price_cache[symbol] = {
                                'price': price,
                                'timestamp': time.time()
                            }
                
                elif 'depth' in arg:
                    # Process orderbook update
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
                
                elif 'trade' in arg:
                    # Process trade update
                    if isinstance(message_data, dict):
                        if symbol not in self.trade_cache:
                            self.trade_cache[symbol] = []
                        trade_data = {
                            'price': float(message_data.get('p', message_data.get('price', 0))),
                            'qty': float(message_data.get('s', message_data.get('size', 0))),
                            'time': int(message_data.get('ts', message_data.get('time', time.time() * 1000))),
                            'is_buyer_maker': message_data.get('side', '') == 'sell'
                        }
                        self.trade_cache[symbol].append(trade_data)
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
                self._report_cache_stats()
                return cache_data['price']
        self.cache_stats['price_misses'] += 1
        self._report_cache_stats()
        return None
    
    def get_orderbook(self, symbol: str, limit: int = 20, max_age_s: int = 30) -> Optional[Dict]:
        """Get orderbook from WebSocket cache"""
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
    
    def get_order_flow_analysis(self, symbol: str, limit: int = 20) -> Dict:
        """Get order flow analysis from WebSocket trade cache"""
        deps = _get_deps()
        try:
            if symbol not in self.trade_cache or not self.trade_cache[symbol]:
                self.cache_stats['flow_misses'] += 1
                self._report_cache_stats()
                return {
                    'buy_pressure': 0, 'sell_pressure': 0, 'cvd': 0, 'net_flow': 0,
                    'trade_count': 0, 'volume_profile': {}, 'institutional_activity': 0,
                    'tape_reading': {}, 'cvd_momentum': 0, 'order_imbalance': 0
                }
            self.cache_stats['flow_hits'] += 1
            self._report_cache_stats()
            trades = self.trade_cache[symbol][-limit:] if len(self.trade_cache[symbol]) >= limit else self.trade_cache[symbol]
            
            buy_volume = 0
            sell_volume = 0
            cvd = 0
            cvd_history = []
            large_trades = 0
            price_levels = {}
            
            for trade in trades:
                volume = trade['price'] * trade['qty']
                price = trade['price']
                if price < 1.0:
                    price_bucket = round(price, 4)
                elif price < 100.0:
                    price_bucket = round(price, 2)
                else:
                    price_bucket = round(price, 1)
                
                if price_bucket not in price_levels:
                    price_levels[price_bucket] = {'buy': 0, 'sell': 0, 'total': 0}
                
                if trade['is_buyer_maker']:
                    sell_volume += volume
                    cvd -= volume
                    price_levels[price_bucket]['sell'] += volume
                else:
                    buy_volume += volume
                    cvd += volume
                    price_levels[price_bucket]['buy'] += volume
                price_levels[price_bucket]['total'] += volume
                cvd_history.append(cvd)
                
                avg_trade_size = (buy_volume + sell_volume) / len(trades) if trades else 0
                if volume > avg_trade_size * 3:
                    large_trades += 1
            
            total_volume = buy_volume + sell_volume
            buy_pressure = deps['safe_division'](buy_volume, total_volume, 0.5) * 100
            sell_pressure = deps['safe_division'](sell_volume, total_volume, 0.5) * 100
            
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
                    'volume_concentration': deps['safe_division'](
                        sum(level[1]['total'] for level in sorted_levels[:3]), total_volume, 0
                    )
                }
            
            tape_reading = {
                'large_trade_ratio': deps['safe_division'](large_trades, len(trades), 0) * 100,
                'avg_trade_size': deps['safe_division'](total_volume, len(trades), 0),
                'trade_frequency': len(trades),
                'volume_acceleration': cvd_momentum
            }
            
            order_imbalance = 0
            try:
                ob = self.get_orderbook(symbol, 10)
                if ob and 'bids' in ob and 'asks' in ob:
                    bid_volume = sum(bid[1] for bid in ob['bids'])
                    ask_volume = sum(ask[1] for ask in ob['asks'])
                    order_imbalance = deps['safe_division'](bid_volume - ask_volume, bid_volume + ask_volume, 0) * 100
            except Exception:
                pass
            
            return {
                'buy_pressure': buy_pressure,
                'sell_pressure': sell_pressure,
                'cvd': cvd,
                'net_flow': buy_volume - sell_volume,
                'trade_count': len(trades),
                'volume_profile': volume_profile,
                'institutional_activity': deps['safe_division'](large_trades, len(trades), 0) * 100,
                'tape_reading': tape_reading,
                'cvd_momentum': cvd_momentum,
                'order_imbalance': order_imbalance
            }
        except Exception as e:
            print(f"{deps['RED']}❌ Enhanced WebSocket order flow analysis error: {e}{deps['RESET']}")
            return {
                'buy_pressure': 0, 'sell_pressure': 0, 'cvd': 0, 'net_flow': 0,
                'trade_count': 0, 'volume_profile': {}, 'institutional_activity': 0,
                'tape_reading': {}, 'cvd_momentum': 0, 'order_imbalance': 0
            }
    
    def _report_cache_stats(self):
        """Report WebSocket cache hit/miss statistics"""
        deps = _get_deps()
        if not getattr(deps['TradingConfig'], 'ENABLE_WS_CACHE_STATS', False):
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
            #     
            #     if deps['output_manager']:
            #         deps['output_manager'].print_static(f"\n{deps['CYAN']}📊 WebSocket Cache Performance (5min):{deps['RESET']}")
            #         deps['output_manager'].print_static(f"  Price: {self.cache_stats['price_hits']}/{total_price} ({price_hit_rate:.1f}% hit rate)")
            #         deps['output_manager'].print_static(f"  Orderbook: {self.cache_stats['orderbook_hits']}/{total_ob} ({ob_hit_rate:.1f}% hit rate)")
            #         deps['output_manager'].print_static(f"  Flow: {self.cache_stats['flow_hits']}/{total_flow} ({flow_hit_rate:.1f}% hit rate)")
            #         total_saved = self.cache_stats['price_hits'] + self.cache_stats['orderbook_hits'] + self.cache_stats['flow_hits']
            #         deps['output_manager'].print_static(f"  {deps['GREEN']}✅ API Calls Saved: ~{total_saved} calls{deps['RESET']}")
            
            # Reset stats (still tracked but not displayed)
            self.cache_stats = {
                'price_hits': 0, 'price_misses': 0,
                'orderbook_hits': 0, 'orderbook_misses': 0,
                'flow_hits': 0, 'flow_misses': 0,
                'last_report': time.time()
            }


