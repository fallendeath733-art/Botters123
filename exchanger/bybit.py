"""
Bybit Exchange Client and WebSocket Manager
"""
import asyncio
import time
import json
import os
import math
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
        # Fallback if imports fail
        def safe_division(n, d, default=0.0):
            if d == 0 or (hasattr(math, 'isnan') and (math.isnan(d) or math.isnan(n))):
                return default
            try:
                result = n / d
                return result if not (hasattr(math, 'isnan') and (math.isnan(result) or math.isinf(result))) else default
            except:
                return default
        return {
            'performance_monitor': None,
            'error_handler': None,
            'safe_division': safe_division,
            'TradingConfig': type('obj', (object,), {'ENABLE_WS_CACHE_STATS': False}),
            'output_manager': None,
            'RED': '', 'GREEN': '', 'YELLOW': '',
            'CYAN': '', 'RESET': ''
        }


class BybitClient(BaseExchangeClient):
    """Bybit Exchange Client"""
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
        super()._respect_rate_limits()

    def get_price_rest(self, symbol: str, category: str = "spot") -> Optional[float]:
        # Early return if symbol is already known to be unsupported (only for spot)
        if category == "spot" and not self.is_symbol_supported_cached(symbol):
            return None
        
        deps = _get_deps()
        start_time = time.time()
        self._respect_rate_limits()
        if time.time() - self.last_cache_cleanup > 300:
            self.cleanup_old_cache()
        self.request_count += 1
        if deps['performance_monitor']:
            deps['performance_monitor'].record_api_call(time.time() - start_time)
        cache_key = f"price_{category}_{symbol}"
        cached_value = self.cache.get(cache_key)
        if cached_value and time.time() - cached_value['timestamp'] < self.cache_ttl['price']:
            return cached_value['data']
        bybit_symbol = symbol.replace('_', '')
        try:
            self.request_timestamps.append(time.time())
            url = f"{self.base_url}/v5/market/tickers"
            params = {
                "category": category,
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
                    # Empty list means symbol not found
                    # Only mark as unsupported for spot market
                    if category == "spot":
                        # Don't print warning here - handled in scan_best_coin_from_watchlist with clearer message
                        self._add_unsupported_symbol(symbol)
                    return None
            else:
                msg = str(data.get('retMsg', '')).lower()
                if 'not supported' in msg or 'invalid' in msg or 'not found' in msg:
                    # Only mark as unsupported for spot market
                    if category == "spot":
                        # Don't print warning here - handled in scan_best_coin_from_watchlist with clearer message
                        self._add_unsupported_symbol(symbol)
                    return None
        except Exception as e:
            # Check if this is an invalid symbol error
            if self._is_invalid_symbol_error(e):
                # Only mark as unsupported for spot market
                if category == "spot":
                    # Don't print warning here - handled in scan_best_coin_from_watchlist with clearer message
                    self._add_unsupported_symbol(symbol)
                return None
            # For other errors, record failure (but don't spam error messages)
            # Only print error for critical issues, not temporary network errors
            error_str = str(e).lower()
            # Silent handling for SSL errors - WebSocket fallback will handle price retrieval
            # SSL errors are often temporary and don't need to spam console
            is_ssl_error = 'ssl' in error_str or 'handshake' in error_str or 'sslv3' in error_str
            is_connection_error = 'connection' in error_str or 'timeout' in error_str or 'name resolution' in error_str or 'temporary failure' in error_str
            
            # Only log non-SSL/connection errors
            if not is_ssl_error and not is_connection_error:
                # Only print non-network errors to reduce clutter
                if deps.get('output_manager'):
                    deps['output_manager'].print_static(
                        f"{deps['YELLOW']}⚠️ Bybit API error for {symbol}: {str(e)[:100]}{deps['RESET']}"
                    )
                else:
                    print(f"{deps['YELLOW']}⚠️ Bybit API error for {symbol}: {str(e)[:100]}{deps['RESET']}")
            
            self.circuit_breaker.record_failure()
            # Only record non-SSL errors to error handler to avoid spam
            if not is_ssl_error and deps['error_handler']:
                deps['error_handler'].record_error("API_Error", f"Price fetch error: {symbol}", {"error": str(e)})
            return None
        print(f"{deps['RED']}❌ Failed to get price from Bybit for {symbol}{deps['RESET']}")
        self.circuit_breaker.record_failure()
        return None

    def get_current_price(self, symbol: str) -> Optional[float]:
        return self.get_price_rest(symbol)
    
    def get_min_order_size(self, symbol: str) -> Optional[float]:
        """Get minimum order size for a symbol from Bybit API"""
        deps = _get_deps()
        try:
            bybit_symbol = symbol.replace('_', '')
            url = f"{self.base_url}/v5/market/instruments-info"
            params = {
                'category': 'spot',
                'symbol': bybit_symbol
            }
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get('retCode') == 0 and 'result' in data:
                result = data['result']
                list_data = result.get('list', [])
                if list_data and len(list_data) > 0:
                    instrument = list_data[0]
                    lot_size_filter = instrument.get('lotSizeFilter', {})
                    min_qty = lot_size_filter.get('minOrderQty', None)
                    if min_qty:
                        try:
                            return float(min_qty)
                        except (ValueError, TypeError):
                            pass
            return None
        except Exception as e:
            # If API fails, return None to use fallback
            return None
    
    def get_min_order_value(self, symbol: str) -> Optional[float]:
        """Get minimum order value (notional) in USDT from Bybit API"""
        deps = _get_deps()
        try:
            bybit_symbol = symbol.replace('_', '')
            url = f"{self.base_url}/v5/market/instruments-info"
            params = {
                'category': 'spot',
                'symbol': bybit_symbol
            }
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get('retCode') == 0 and 'result' in data:
                result = data['result']
                list_data = result.get('list', [])
                if list_data and len(list_data) > 0:
                    instrument = list_data[0]
                    lot_size_filter = instrument.get('lotSizeFilter', {})
                    min_order_amt = lot_size_filter.get('minOrderAmt', None)
                    if min_order_amt:
                        try:
                            return float(min_order_amt)
                        except (ValueError, TypeError):
                            pass
            return None
        except Exception as e:
            # If API fails, return None to use fallback
            return None
    
    def cancel_all_spot_orders(self) -> bool:
        """Cancel all pending orders in spot trading"""
        deps = _get_deps()
        try:
            import core
            if not core.CONFIG:
                core.load_config()
            exchange_name = core.SELECTED_EXCHANGE
            if not core.CONFIG or exchange_name not in core.CONFIG:
                return False
            
            if exchange_name != 'BYBIT':
                return False
            
            api_key = core.CONFIG[exchange_name].get('API_KEY', '')
            api_secret = core.CONFIG[exchange_name].get('API_SECRET', '')
            testnet = core.CONFIG[exchange_name].get('TESTNET', 'false')
            
            placeholder_keys = ['your_bybit_api_key_here', 'your_okx_api_key_here', 
                              'your_binance_api_key_here', 'your_bitget_api_key_here',
                              'your_kucoin_api_key_here', 'your_mexc_api_key_here',
                              'your_gateio_api_key_here', '']
            
            if not api_key or not api_secret or api_key in placeholder_keys:
                return False
            
            is_testnet = str(testnet).lower() == 'true'
            base_url = "https://api-testnet.bybit.com" if is_testnet else "https://api.bybit.com"
            
            import hmac
            import hashlib
            import urllib.parse
            from time import time
            
            timestamp = str(int(time() * 1000))
            recv_window = "5000"
            
            # For Bybit v5 POST /v5/order/cancel-all
            # According to Bybit docs, for POST requests with body:
            # Signature = HMAC-SHA256(timestamp + api_key + recv_window + request_body)
            # Request body should be JSON string
            params = {
                "category": "spot"
            }
            
            # Convert params to JSON string (must match exactly what's sent)
            import json
            # Use json.dumps with no spaces and sorted keys
            body_json_str = json.dumps(params, separators=(',', ':'))
            
            # Build payload: timestamp + api_key + recv_window + request_body
            payload = f"{timestamp}{api_key}{recv_window}{body_json_str}"
            signature = hmac.new(
                bytes(api_secret, "utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            
            headers = {
                "X-BAPI-API-KEY": api_key,
                "X-BAPI-SIGN": signature,
                "X-BAPI-SIGN-TYPE": "2",
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": recv_window,
                "Content-Type": "application/json"
            }
            
            url = f"{base_url}/v5/order/cancel-all"
            # POST with JSON body - use data parameter to send exact JSON string
            response = self.session.post(url, data=body_json_str, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            ret_code = data.get('retCode', -1)
            if ret_code == 0:
                result = data.get('result', {})
                list_data = result.get('list', [])
                
                # CRITICAL: Only count orders from list_data, not from 'success' field
                # The 'success' field might be misleading (e.g., "1" even when no orders cancelled)
                # We only trust actual list_data which contains the cancelled orders
                cancelled_count = len(list_data) if list_data else 0
                
                # Show details of cancelled orders
                coins = set()
                orders_details = []
                
                if list_data and len(list_data) > 0:
                    # If we have list data, use it
                    for order in list_data:
                        # Only use symbol field, not orderId (orderId is numeric, not a coin name)
                        symbol = order.get('symbol', '')
                        side = order.get('side', '')
                        qty = order.get('qty', '') or order.get('orderQty', '')
                        price = order.get('price', '') or order.get('orderPrice', '')
                        
                        # Only process if we have a valid symbol (not orderId)
                        if symbol and not symbol.isdigit():
                            # Extract coin name from symbol (e.g., ZETAUSDT -> ZETA)
                            coin = symbol.replace('USDT', '').replace('BTC', '').replace('ETH', '').replace('USDC', '')
                            if coin and len(coin) > 0 and not coin.isdigit():
                                coins.add(coin)
                            orders_details.append({
                                'symbol': symbol,
                                'side': side,
                                'qty': qty,
                                'price': price
                            })
                
                # Display result with detailed message using helper function
                message = self._format_cancel_orders_message(cancelled_count, coins)
                print(message)
                
                if cancelled_count > 0:
                    # Show order details if available and valid
                    if orders_details and len(orders_details) <= 5:
                        for order in orders_details:
                            symbol = order.get('symbol', '')
                            side = order.get('side', '')
                            qty = order.get('qty', '')
                            price = order.get('price', '')
                            
                            if side and qty and price:
                                print(f"{deps['CYAN']}   - {symbol}: {side} {qty} @ {price}{deps['RESET']}")
                            elif symbol and not symbol.isdigit():
                                print(f"{deps['CYAN']}   - {symbol}{deps['RESET']}")
                
                return True
            else:
                ret_msg = data.get('retMsg', 'Unknown error')
                # If error is "no orders", that's actually success
                if 'no orders' in ret_msg.lower() or 'no open orders' in ret_msg.lower():
                    message = self._format_cancel_orders_message(0)
                    print(message)
                    return True
                print(f"{deps['RED']}❌ Failed to cancel orders: {ret_msg}{deps['RESET']}")
                return False
        except Exception as e:
            print(f"{deps['RED']}❌ Error cancelling orders: {str(e)}{deps['RESET']}")
            return False

    def fetch_candles(self, symbol: str, interval: str = "5m", limit: int = 200, category: str = "spot") -> Optional[List[Dict]]:
        deps = _get_deps()
        start_time = time.time()
        self._respect_rate_limits()
        if time.time() - self.last_cache_cleanup > 300:
            self.cleanup_old_cache()
        self.request_count += 1
        if deps['performance_monitor']:
            deps['performance_monitor'].record_api_call(time.time() - start_time)
        cache_key = f"candles_{category}_{symbol}_{interval}_{limit}"
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
                "category": category,
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
            if not self.is_symbol_supported_cached(symbol):
                # Symbol not supported - silently skip (likely futures-only coin)
                return None
            # For supported symbols, silence the warning to reduce spam
            # The error is already handled by returning None
            # print(f"{deps['YELLOW']}⚠️ No candle data from Bybit for {symbol}{deps['RESET']}")
            self.circuit_breaker.record_failure()
            return None
        except Exception as e:
            error_str = str(e).lower()
            # Silent handling for SSL errors - they're often temporary
            is_ssl_error = 'ssl' in error_str or 'handshake' in error_str or 'sslv3' in error_str
            is_connection_error = 'connection' in error_str or 'timeout' in error_str
            
            # Only log non-SSL errors
            if not is_ssl_error and not is_connection_error:
                if deps.get('output_manager'):
                    deps['output_manager'].print_static(
                        f"{deps['RED']}❌ Bybit candle error for {symbol}: {str(e)[:100]}{deps['RESET']}"
                    )
                else:
                    print(f"{deps['RED']}❌ Bybit candle error for {symbol}: {str(e)[:100]}{deps['RESET']}")
            
            self.circuit_breaker.record_failure()
            # Only record non-SSL errors to error handler
            if not is_ssl_error and deps['error_handler']:
                deps['error_handler'].record_error("API_Error", f"Candle fetch error: {symbol}", {"error": str(e)})
            return None

    def get_aggregated_trades(self, symbol: str, limit: int = 500) -> Optional[List[Dict]]:
        deps = _get_deps()
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
                    self._add_unsupported_symbol(symbol)
                    return None
                print(f"{deps['YELLOW']}⚠️ Bybit aggregated trades error: {data.get('retMsg', 'Unknown error')}{deps['RESET']}")
                return None
        except Exception as e:
            error_str = str(e).lower()
            # Silent handling for SSL/connection errors
            is_ssl_error = 'ssl' in error_str or 'handshake' in error_str or 'sslv3' in error_str
            is_connection_error = 'connection' in error_str or 'timeout' in error_str
            
            # Only log non-SSL errors
            if not is_ssl_error and not is_connection_error:
                if deps.get('output_manager'):
                    deps['output_manager'].print_static(
                        f"{deps['RED']}❌ Bybit aggregated trades error: {str(e)[:100]}{deps['RESET']}"
                    )
                else:
                    print(f"{deps['RED']}❌ Bybit aggregated trades error: {str(e)[:100]}{deps['RESET']}")
            
            self.circuit_breaker.record_failure()
            # Only record non-SSL errors
            if not is_ssl_error and deps['error_handler']:
                deps['error_handler'].record_error("API_Error", f"Aggregated trades error: {symbol}", {"error": str(e)})
            return None

    def get_orderbook(self, symbol: str, limit: int = 100) -> Optional[Dict]:
        deps = _get_deps()
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
                    self._add_unsupported_symbol(symbol)
                    return None
                print(f"{deps['YELLOW']}⚠️ Bybit orderbook error: {data.get('retMsg', 'Unknown error')}{deps['RESET']}")
                return None
        except Exception as e:
            print(f"{deps['RED']}❌ Bybit orderbook error: {e}{deps['RESET']}")
            self.circuit_breaker.record_failure()
            return None

    def get_order_flow_analysis(self, symbol: str, limit: int = 20) -> Dict:
        deps = _get_deps()
        try:
            trades = self.get_aggregated_trades(symbol, limit)
            if not trades:
                return {
                    'buy_pressure': 0,
                    'sell_pressure': 0,
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
            print(f"{deps['RED']}❌ Enhanced order flow analysis error: {e}{deps['RESET']}")
            return {
                'buy_pressure': 0,
                'sell_pressure': 0,
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
    
    def get_account_info(self) -> Optional[Dict]:
        """Get account information including balance"""
        deps = _get_deps()
        try:
            # Load API credentials - use core module directly
            import core
            # Ensure CONFIG is loaded
            if not core.CONFIG:
                core.load_config()
            exchange_name = core.SELECTED_EXCHANGE
            if not core.CONFIG or exchange_name not in core.CONFIG:
                return None
            
            api_key = core.CONFIG[exchange_name].get('API_KEY', '')
            api_secret = core.CONFIG[exchange_name].get('API_SECRET', '')
            testnet = core.CONFIG[exchange_name].get('TESTNET', 'false')
            
            # Only works for BYBIT exchange
            if exchange_name != 'BYBIT':
                return None
            
            placeholder_keys = ['your_bybit_api_key_here', 'your_okx_api_key_here', 
                              'your_binance_api_key_here', 'your_bitget_api_key_here',
                              'your_kucoin_api_key_here', 'your_mexc_api_key_here',
                              'your_gateio_api_key_here', '']
            
            if not api_key or not api_secret or api_key in placeholder_keys:
                return None
            
            # Check if testnet mode
            is_testnet = str(testnet).lower() == 'true'
            base_url = "https://api-testnet.bybit.com" if is_testnet else "https://api.bybit.com"
            
            # Get wallet balance using Bybit API
            import hmac
            import hashlib
            import urllib.parse
            from time import time
            
            timestamp = str(int(time() * 1000))
            recv_window = "5000"
            
            # Get wallet balance - Bybit v5 uses UNIFIED account type
            # Don't specify coin parameter to get all coins
            params = {
                "accountType": "UNIFIED"
            }
            param_str = urllib.parse.urlencode(sorted(params.items()))
            payload = f"{timestamp}{api_key}{recv_window}{param_str}"
            signature = hmac.new(
                bytes(api_secret, "utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            
            headers = {
                "X-BAPI-API-KEY": api_key,
                "X-BAPI-SIGN": signature,
                "X-BAPI-SIGN-TYPE": "2",
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": recv_window,
                "Content-Type": "application/json"
            }
            
            url = f"{base_url}/v5/account/wallet-balance"
            response = self.session.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            ret_code = data.get('retCode', -1)
            ret_msg = data.get('retMsg', '')
            
            # Check for permission errors first
            if ret_code != 0:
                if ret_code in [10003, 10004, 10005]:
                    # Permission denied - API key might be read-only
                    return None
            
            # Check if we have result data
            if not data.get('result'):
                # No result - if retCode == 0, return empty account
                if ret_code == 0:
                    return {
                        'usdt_balance': 0.0,
                        'total_equity': 0.0,
                        'spot_balances': [],
                        'testnet': is_testnet,
                        'account_type': 'SPOT',
                        'status': 'connected'
                    }
                return None
            
            # Process result data
            wallet = data['result']
            coin_list = wallet.get('list', [])
            
            if not coin_list or len(coin_list) == 0:
                # Empty coin list
                if ret_code == 0:
                    return {
                        'usdt_balance': 0.0,
                        'total_equity': 0.0,
                        'spot_balances': [],
                        'testnet': is_testnet,
                        'account_type': 'SPOT',
                        'status': 'connected'
                    }
                return None
            
            coins = coin_list[0].get('coin', [])
            if not coins:
                # No coins in list
                if ret_code == 0:
                    return {
                        'usdt_balance': 0.0,
                        'total_equity': 0.0,
                        'spot_balances': [],
                        'testnet': is_testnet,
                        'account_type': 'SPOT',
                        'status': 'connected'
                    }
                return None
            
            usdt_balance = 0.0
            total_equity = 0.0
            spot_balances = []
            
            for coin in coins:
                try:
                    coin_name = coin.get('coin', '')
                    if not coin_name:
                        continue
                    
                    # Handle empty strings or None values
                    wallet_balance_str = coin.get('walletBalance', '0') or '0'
                    locked_str = coin.get('locked', '0') or '0'
                    available_balance_str = coin.get('availableToWithdraw', '') or ''
                    equity_str = coin.get('equity', '0') or '0'
                    
                    try:
                        wallet_balance = float(wallet_balance_str)
                        locked = float(locked_str)
                        equity = float(equity_str)
                        
                        # Calculate available balance
                        # If availableToWithdraw is empty string or None, calculate from walletBalance - locked
                        if available_balance_str and available_balance_str.strip():
                            try:
                                available_balance = float(available_balance_str)
                            except (ValueError, TypeError):
                                # If availableToWithdraw is invalid, calculate from walletBalance - locked
                                available_balance = max(0, wallet_balance - locked)
                        else:
                            # availableToWithdraw is empty (common for very small balances)
                            # Calculate available as walletBalance - locked
                            available_balance = max(0, wallet_balance - locked)
                    except (ValueError, TypeError):
                        # Skip coins with invalid balance data
                        continue
                    
                    # Only include coins with balance > 0 and sufficient for trading
                    # Minimum trading amount varies by coin, but typically need at least 0.00001 or equivalent
                    # For very small amounts (< 0.00001), they might not be tradeable
                    min_trading_threshold = 0.00001  # Minimum amount to be tradeable (adjust based on exchange)
                    
                    if wallet_balance > 0 and (wallet_balance >= min_trading_threshold or available_balance >= min_trading_threshold):
                        spot_balances.append({
                            'coin': coin_name,
                            'wallet_balance': wallet_balance,
                            'available': available_balance,
                            'equity': equity
                        })
                    
                    if coin_name == 'USDT':
                        usdt_balance = wallet_balance
                        total_equity = equity
                except Exception:
                    # Skip invalid coin data
                    continue
            
            # Sort by coin name alphabetically
            spot_balances.sort(key=lambda x: x['coin'])
            
            # Return account info if we have any balances
            if spot_balances or usdt_balance > 0:
                return {
                    'usdt_balance': usdt_balance,
                    'total_equity': total_equity,
                    'spot_balances': spot_balances,
                    'testnet': is_testnet,
                    'account_type': 'SPOT',
                    'status': 'connected' if ret_code == 0 else 'partial'
                }
            
            # No balances but account exists
            if ret_code == 0:
                return {
                    'usdt_balance': 0.0,
                    'total_equity': 0.0,
                    'spot_balances': [],
                    'testnet': is_testnet,
                    'account_type': 'SPOT',
                    'status': 'connected'
                }
            
            return None
        except Exception as e:
            # Log error for debugging but don't expose sensitive info
            error_str = str(e).lower()
            
            # Try to extract error details from response
            try:
                import json
                if hasattr(e, 'response') and hasattr(e.response, 'json'):
                    error_data = e.response.json()
                    ret_code = error_data.get('retCode', '')
                    ret_msg = error_data.get('retMsg', '')
                    
                    # Check if it's a permission error
                    if ret_code in ['10003', '10004', '10005']:  # Permission errors
                        # Permission denied - return None
                        return None
                    
                    # For other errors, try to see if we can still get partial data
                    # But if we're here, the request failed, so return None
                    return None
            except:
                pass
            
            # For network errors or other exceptions, return None
            if 'connection' in error_str or 'timeout' in error_str:
                # Network error - might be temporary
                return None
            elif 'retcode' in error_str or 'retmsg' in error_str:
                # API error - might be permission or invalid request
                return None
            
            # Return None for any other error
            return None


class BybitWebSocketManager(BaseWebSocketManager):
    """Bybit WebSocket Manager"""
    def __init__(self):
        super().__init__("BYBIT")
        self.base_url = "wss://stream.bybit.com/v5/public/spot"
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
        deps = _get_deps()
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
        deps = _get_deps()
        try:
            ws_symbol = symbol.replace('_', '')
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
                        print(f"{deps['YELLOW']}⚠️ WebSocket message error for {symbol}: {e}{deps['RESET']}")
        except websockets.exceptions.ConnectionClosed:
            # WebSocket connection closed messages disabled to reduce clutter
            # This is normal when connection drops, will auto-reconnect
            # print(f"{deps['YELLOW']}⚠️ WebSocket connection closed for {symbol}{deps['RESET']}")
            pass
        except Exception as e:
            print(f"{deps['RED']}❌ WebSocket error for {symbol}: {e}{deps['RESET']}")
        finally:
            if symbol in self.connections:
                del self.connections[symbol]

    async def _process_message(self, symbol: str, data: dict):
        deps = _get_deps()
        try:
            if 'success' in data:
                if data.get('success'):
                    pass
                else:
                    # Silent skip for invalid symbols (not available in spot market)
                    # Error message is expected for futures-only coins like JELLYJELLY
                    # Mark symbol as unsupported to avoid future attempts
                    ret_msg = data.get('ret_msg', 'Unknown error')
                    if 'invalid symbol' in ret_msg.lower():
                        # Silently skip - coin not available in spot market
                        pass
                    else:
                        # Only show non-invalid-symbol errors
                        print(f"{deps['YELLOW']}⚠️ WebSocket subscription warning: {ret_msg}{deps['RESET']}")
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
                except (KeyError, ValueError, TypeError, IndexError):
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
                except (KeyError, ValueError, TypeError, IndexError):
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
                except (KeyError, ValueError, TypeError):
                    pass
            elif 'kline.' in topic:
                try:
                    parts = topic.split('.')
                    interval_code = parts[1] if len(parts) > 1 else None
                    interval_map = {
                        '1': '1m', '3': '3m', '5': '5m', '15m': '15m', '30': '30m',
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
                            if all(x is not None for x in [kline['t'], kline['T'], kline['o'], kline['h'], kline['l'], kline['c'], kline['v']]):
                                self.update_candle_cache(symbol, internal_interval, kline)
                except Exception:
                    pass
        except Exception as e:
            print(f"{deps['RED']}❌ WebSocket message processing error: {e}{deps['RESET']}")

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

    def get_order_flow_analysis(self, symbol: str, limit: int = 20) -> Dict:
        deps = _get_deps()
        try:
            if symbol not in self.trade_cache or not self.trade_cache[symbol]:
                self.cache_stats['flow_misses'] += 1
                self._report_cache_stats()
                return {
                    'buy_pressure': 0,
                    'sell_pressure': 0,
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
                ob = self.orderbook_cache.get(symbol)
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
                'buy_pressure': 0,
                'sell_pressure': 0,
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
        deps = _get_deps()
        try:
            # Need to get client reference - will be passed from core
            from core import bybit_client
            candles = bybit_client.fetch_candles(symbol, interval, limit)
            if candles:
                self.candle_cache[f"{symbol}_{interval}"] = {
                    'candles': candles,
                    'timestamp': time.time()
                }
        except Exception as e:
            print(f"{deps['RED']}❌ WebSocket candle cache initialization error: {e}{deps['RESET']}")

    def update_candle_cache(self, symbol: str, interval: str, kline_data: dict):
        deps = _get_deps()
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
            print(f"{deps['RED']}❌ WebSocket candle cache update error: {e}{deps['RESET']}")

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
            #     if deps['output_manager']:
            #         deps['output_manager'].print_static(f"\n{deps['CYAN']}📊 WebSocket Cache Performance (5min):{deps['RESET']}")
            #         deps['output_manager'].print_static(f"  Price: {self.cache_stats['price_hits']}/{total_price} ({price_hit_rate:.1f}% hit rate)")
            #         deps['output_manager'].print_static(f"  Orderbook: {self.cache_stats['orderbook_hits']}/{total_ob} ({ob_hit_rate:.1f}% hit rate)")
            #         deps['output_manager'].print_static(f"  Flow: {self.cache_stats['flow_hits']}/{total_flow} ({flow_hit_rate:.1f}% hit rate)")
            #         total_saved = self.cache_stats['price_hits'] + self.cache_stats['orderbook_hits'] + self.cache_stats['flow_hits']
            #         deps['output_manager'].print_static(f"  {deps['GREEN']}✅ API Calls Saved: ~{total_saved} calls{deps['RESET']}")
            
            # Reset stats (still tracked but not displayed)
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

