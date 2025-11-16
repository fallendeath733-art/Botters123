"""
VWAP (Volume Weighted Average Price) Analysis Module
VWAP memberikan reference point untuk trend analysis dan entry/exit signals
"""

from typing import Dict, List, Optional, Tuple
import time


def calculate_vwap(candles: List[Dict], timeframe: str = '1h') -> Optional[float]:
    """
    Calculate VWAP (Volume Weighted Average Price) from candles.
    
    Args:
        candles: List of candle data with 'high', 'low', 'close', 'volume'
        timeframe: Timeframe for reference (e.g., '1h', '4h', '1d')
    
    Returns:
        VWAP price or None if calculation fails
    """
    if not candles or len(candles) == 0:
        return None
    
    try:
        total_volume = 0.0
        volume_weighted_price = 0.0
        
        for candle in candles:
            volume = float(candle.get('volume', 0))
            if volume <= 0:
                continue
            
            # Use typical price (high + low + close) / 3 for VWAP calculation
            high = float(candle.get('high', 0))
            low = float(candle.get('low', 0))
            close = float(candle.get('close', 0))
            
            if high <= 0 or low <= 0 or close <= 0:
                continue
            
            typical_price = (high + low + close) / 3.0
            volume_weighted_price += typical_price * volume
            total_volume += volume
        
        if total_volume > 0:
            return volume_weighted_price / total_volume
        else:
            return None
    
    except Exception:
        return None


def calculate_multi_timeframe_vwap(
    get_candles_func,
    symbol: str,
    timeframes: List[str] = ['1h', '4h', '1d'],
    periods: Dict[str, int] = None
) -> Dict[str, Optional[float]]:
    """
    Calculate VWAP for multiple timeframes.
    
    Args:
        get_candles_func: Function to get candles (async or sync)
        symbol: Trading symbol
        timeframes: List of timeframes to calculate VWAP for
        periods: Dict of periods for each timeframe (default: 24 for 1h, 6 for 4h, 7 for 1d)
    
    Returns:
        Dict with timeframe as key and VWAP price as value
    """
    if periods is None:
        periods = {
            '1h': 24,   # 24 hours = 1 day
            '4h': 6,    # 6 * 4h = 24 hours = 1 day
            '1d': 7     # 7 days = 1 week
        }
    
    vwap_results = {}
    
    for timeframe in timeframes:
        period = periods.get(timeframe, 24)
        try:
            # Try to get candles (handle both async and sync)
            if hasattr(get_candles_func, '__call__'):
                # Check if it's async
                import asyncio
                if asyncio.iscoroutinefunction(get_candles_func):
                    # For async, we'll need to await it in the caller
                    vwap_results[timeframe] = None  # Will be calculated in caller
                else:
                    candles = get_candles_func(symbol, timeframe, period)
                    vwap_results[timeframe] = calculate_vwap(candles, timeframe)
            else:
                vwap_results[timeframe] = None
        except Exception:
            vwap_results[timeframe] = None
    
    return vwap_results


async def calculate_multi_timeframe_vwap_async(
    get_candles_func,
    symbol: str,
    timeframes: List[str] = ['1h', '4h', '1d'],
    periods: Dict[str, int] = None
) -> Dict[str, Optional[float]]:
    """
    Calculate VWAP for multiple timeframes (async version).
    
    Args:
        get_candles_func: Async function to get candles
        symbol: Trading symbol
        timeframes: List of timeframes to calculate VWAP for
        periods: Dict of periods for each timeframe
    
    Returns:
        Dict with timeframe as key and VWAP price as value
    """
    if periods is None:
        periods = {
            '1h': 24,
            '4h': 6,
            '1d': 7
        }
    
    vwap_results = {}
    
    for timeframe in timeframes:
        period = periods.get(timeframe, 24)
        try:
            candles = await get_candles_func(symbol, timeframe, period)
            vwap_results[timeframe] = calculate_vwap(candles, timeframe)
        except Exception:
            vwap_results[timeframe] = None
    
    return vwap_results


def analyze_vwap_trend(
    current_price: float,
    vwap: Optional[float],
    vwap_multi: Optional[Dict[str, Optional[float]]] = None
) -> Dict[str, any]:
    """
    Analyze trend berdasarkan VWAP.
    
    Args:
        current_price: Current market price
        vwap: VWAP value (single timeframe)
        vwap_multi: Optional multi-timeframe VWAP values
    
    Returns:
        Dict dengan:
            - trend: 'BULLISH', 'BEARISH', or 'NEUTRAL'
            - price_vs_vwap: Price difference from VWAP in %
            - strength: Trend strength (0-1)
            - multi_timeframe_alignment: Alignment across timeframes
    """
    if vwap is None or vwap <= 0:
        return {
            'trend': 'NEUTRAL',
            'price_vs_vwap': 0.0,
            'strength': 0.0,
            'multi_timeframe_alignment': 'UNKNOWN',
            'error': 'VWAP not available'
        }
    
    try:
        price_vs_vwap_pct = ((current_price - vwap) / vwap) * 100
        
        # Determine trend
        if price_vs_vwap_pct > 2.0:  # Price > 2% above VWAP
            trend = 'BULLISH'
            strength = min(1.0, abs(price_vs_vwap_pct) / 5.0)  # Normalize to 0-1
        elif price_vs_vwap_pct < -2.0:  # Price < 2% below VWAP
            trend = 'BEARISH'
            strength = min(1.0, abs(price_vs_vwap_pct) / 5.0)
        else:
            trend = 'NEUTRAL'
            strength = 0.0
        
        # Analyze multi-timeframe alignment if available
        multi_timeframe_alignment = 'UNKNOWN'
        if vwap_multi:
            bullish_count = 0
            bearish_count = 0
            
            for tf, tf_vwap in vwap_multi.items():
                if tf_vwap is None or tf_vwap <= 0:
                    continue
                tf_price_vs_vwap = ((current_price - tf_vwap) / tf_vwap) * 100
                if tf_price_vs_vwap > 1.0:
                    bullish_count += 1
                elif tf_price_vs_vwap < -1.0:
                    bearish_count += 1
            
            total_tf = bullish_count + bearish_count
            if total_tf > 0:
                if bullish_count > bearish_count:
                    multi_timeframe_alignment = 'BULLISH'
                elif bearish_count > bullish_count:
                    multi_timeframe_alignment = 'BEARISH'
                else:
                    multi_timeframe_alignment = 'MIXED'
        
        return {
            'trend': trend,
            'price_vs_vwap': price_vs_vwap_pct,
            'strength': strength,
            'multi_timeframe_alignment': multi_timeframe_alignment,
            'vwap': vwap,
            'current_price': current_price
        }
    
    except Exception as e:
        return {
            'trend': 'NEUTRAL',
            'price_vs_vwap': 0.0,
            'strength': 0.0,
            'multi_timeframe_alignment': 'UNKNOWN',
            'error': str(e)
        }


def get_vwap_entry_signal(
    vwap_analysis: Dict,
    min_strength: float = 0.3
) -> Tuple[bool, str]:
    """
    Get entry signal berdasarkan VWAP analysis.
    
    Args:
        vwap_analysis: Result from analyze_vwap_trend()
        min_strength: Minimum strength required (0-1)
    
    Returns:
        Tuple of (should_enter, reason)
    """
    if 'error' in vwap_analysis:
        return False, f"VWAP error: {vwap_analysis['error']}"
    
    trend = vwap_analysis.get('trend', 'NEUTRAL')
    strength = vwap_analysis.get('strength', 0.0)
    alignment = vwap_analysis.get('multi_timeframe_alignment', 'UNKNOWN')
    
    if trend == 'BEARISH':
        return False, f"Bearish VWAP trend (price {vwap_analysis.get('price_vs_vwap', 0):.2f}% below VWAP)"
    
    if strength < min_strength:
        return False, f"VWAP trend strength too low: {strength:.2f} < {min_strength:.2f}"
    
    if trend == 'BULLISH':
        alignment_info = f" ({alignment})" if alignment != 'UNKNOWN' else ""
        return True, f"Bullish VWAP trend (price {vwap_analysis.get('price_vs_vwap', 0):.2f}% above VWAP, strength: {strength:.2f}){alignment_info}"
    else:
        return False, f"Neutral VWAP trend (price {vwap_analysis.get('price_vs_vwap', 0):.2f}% vs VWAP)"


def get_vwap_exit_signal(
    vwap_analysis: Dict,
    entry_price: float,
    profit_target_pct: float = 2.0
) -> Tuple[bool, str]:
    """
    Get exit signal berdasarkan VWAP analysis.
    
    Args:
        vwap_analysis: Result from analyze_vwap_trend()
        entry_price: Entry price
        profit_target_pct: Profit target percentage (default 2%)
    
    Returns:
        Tuple of (should_exit, reason)
    """
    if 'error' in vwap_analysis:
        return False, f"VWAP error: {vwap_analysis['error']}"
    
    current_price = vwap_analysis.get('current_price', 0)
    if current_price <= 0 or entry_price <= 0:
        return False, "Invalid price data"
    
    profit_pct = ((current_price - entry_price) / entry_price) * 100
    trend = vwap_analysis.get('trend', 'NEUTRAL')
    
    # Exit if profit target reached
    if profit_pct >= profit_target_pct:
        return True, f"Profit target reached: {profit_pct:.2f}% >= {profit_target_pct:.2f}%"
    
    # Exit if trend turns bearish
    if trend == 'BEARISH' and profit_pct > 0:
        return True, f"VWAP trend turned bearish (profit: {profit_pct:.2f}%)"
    
    # Exit if price falls below VWAP after being above
    price_vs_vwap = vwap_analysis.get('price_vs_vwap', 0)
    if price_vs_vwap < -1.0 and profit_pct > 0:
        return True, f"Price fell below VWAP (price_vs_vwap: {price_vs_vwap:.2f}%, profit: {profit_pct:.2f}%)"
    
    return False, f"No exit signal (trend: {trend}, profit: {profit_pct:.2f}%)"


__all__ = [
    'calculate_vwap',
    'calculate_multi_timeframe_vwap',
    'calculate_multi_timeframe_vwap_async',
    'analyze_vwap_trend',
    'get_vwap_entry_signal',
    'get_vwap_exit_signal'
]

