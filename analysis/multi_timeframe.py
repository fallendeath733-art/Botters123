"""
Multi-Timeframe Confirmation Module
Menggunakan trend dari timeframe lebih tinggi untuk konfirmasi entry di timeframe lebih rendah
"""

from typing import Dict, Optional, Tuple
from enum import Enum


class TrendDirection(Enum):
    """Trend direction untuk multi-timeframe analysis"""
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    SIDEWAYS = "SIDEWAYS"
    UNKNOWN = "UNKNOWN"


def calculate_trend_from_candles(candles: list) -> TrendDirection:
    """
    Calculate trend direction dari candles menggunakan SMA dan price action
    
    Args:
        candles: List of candle dicts dengan keys 'close', 'high', 'low'
    
    Returns:
        TrendDirection: UPTREND, DOWNTREND, SIDEWAYS, atau UNKNOWN
    """
    if not candles or len(candles) < 20:
        return TrendDirection.UNKNOWN
    
    try:
        # Get closing prices
        closes = [float(c.get('close', 0)) for c in candles if c.get('close')]
        if len(closes) < 20:
            return TrendDirection.UNKNOWN
        
        # Calculate SMA 20 and SMA 50
        sma_20 = sum(closes[-20:]) / 20
        sma_50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else sma_20
        
        # Current price
        current_price = closes[-1]
        
        # Price position relative to SMAs
        price_above_sma20 = current_price > sma_20
        price_above_sma50 = current_price > sma_50
        sma20_above_sma50 = sma_20 > sma_50
        
        # Higher highs and higher lows check (last 10 candles)
        if len(closes) >= 10:
            recent_highs = [float(c.get('high', c.get('close', 0))) for c in candles[-10:]]
            recent_lows = [float(c.get('low', c.get('close', 0))) for c in candles[-10:]]
            
            highest_recent = max(recent_highs)
            lowest_recent = min(recent_lows)
            
            # Check for higher highs (uptrend) or lower lows (downtrend)
            prev_highs = [float(c.get('high', c.get('close', 0))) for c in candles[-20:-10]] if len(candles) >= 20 else recent_highs
            prev_lows = [float(c.get('low', c.get('close', 0))) for c in candles[-20:-10]] if len(candles) >= 20 else recent_lows
            
            prev_highest = max(prev_highs) if prev_highs else highest_recent
            prev_lowest = min(prev_lows) if prev_lows else lowest_recent
            
            higher_highs = highest_recent > prev_highest
            higher_lows = lowest_recent > prev_lowest
            lower_highs = highest_recent < prev_highest
            lower_lows = lowest_recent < prev_lowest
        else:
            higher_highs = False
            higher_lows = False
            lower_highs = False
            lower_lows = False
        
        # Determine trend
        uptrend_signals = 0
        downtrend_signals = 0
        
        if price_above_sma20:
            uptrend_signals += 1
        else:
            downtrend_signals += 1
        
        if price_above_sma50:
            uptrend_signals += 1
        else:
            downtrend_signals += 1
        
        if sma20_above_sma50:
            uptrend_signals += 1
        else:
            downtrend_signals += 1
        
        if higher_highs and higher_lows:
            uptrend_signals += 2
        elif lower_highs and lower_lows:
            downtrend_signals += 2
        
        # Determine final trend
        if uptrend_signals >= 4:
            return TrendDirection.UPTREND
        elif downtrend_signals >= 4:
            return TrendDirection.DOWNTREND
        elif uptrend_signals > downtrend_signals:
            return TrendDirection.UPTREND
        elif downtrend_signals > uptrend_signals:
            return TrendDirection.DOWNTREND
        else:
            return TrendDirection.SIDEWAYS
    
    except Exception:
        return TrendDirection.UNKNOWN


def calculate_momentum_from_candles(candles: list) -> float:
    """
    Calculate momentum dari candles (rate of change)
    
    Args:
        candles: List of candle dicts
    
    Returns:
        Momentum value (positive = bullish, negative = bearish)
    """
    if not candles or len(candles) < 10:
        return 0.0
    
    try:
        closes = [float(c.get('close', 0)) for c in candles if c.get('close')]
        if len(closes) < 10:
            return 0.0
        
        # Calculate momentum as rate of change over last 10 candles
        current_price = closes[-1]
        price_10_ago = closes[-10] if len(closes) >= 10 else closes[0]
        
        if price_10_ago > 0:
            momentum = ((current_price - price_10_ago) / price_10_ago) * 100
            return momentum
        else:
            return 0.0
    
    except Exception:
        return 0.0


async def check_multi_timeframe_alignment(
    get_candles_func,
    symbol: str,
    timeframes: list = ['15m', '1h', '4h'],
    current_price: Optional[float] = None
) -> Dict[str, any]:
    """
    Check alignment across multiple timeframes
    
    Args:
        get_candles_func: Async function to get candles
        symbol: Trading symbol
        timeframes: List of timeframes to check
        current_price: Optional current price (will fetch if not provided)
    
    Returns:
        Dict dengan:
            - alignment: 'BULLISH', 'BEARISH', 'MIXED', or 'UNKNOWN'
            - trend_by_tf: Dict of trend for each timeframe
            - momentum_by_tf: Dict of momentum for each timeframe
            - strength: Alignment strength (0-1)
    """
    try:
        trend_by_tf = {}
        momentum_by_tf = {}
        
        for tf in timeframes:
            try:
                candles = await get_candles_func(symbol, tf, 50)
                if candles and len(candles) >= 20:
                    trend = calculate_trend_from_candles(candles)
                    momentum = calculate_momentum_from_candles(candles)
                    trend_by_tf[tf] = trend
                    momentum_by_tf[tf] = momentum
                else:
                    trend_by_tf[tf] = TrendDirection.UNKNOWN
                    momentum_by_tf[tf] = 0.0
            except Exception:
                trend_by_tf[tf] = TrendDirection.UNKNOWN
                momentum_by_tf[tf] = 0.0
        
        # Count trends
        bullish_count = 0
        bearish_count = 0
        unknown_count = 0
        
        for trend in trend_by_tf.values():
            if trend == TrendDirection.UPTREND:
                bullish_count += 1
            elif trend == TrendDirection.DOWNTREND:
                bearish_count += 1
            else:
                unknown_count += 1
        
        # Determine alignment
        total_tf = len(timeframes)
        if bullish_count > total_tf * 0.6:  # > 60% bullish
            alignment = 'BULLISH'
            strength = bullish_count / total_tf
        elif bearish_count > total_tf * 0.6:  # > 60% bearish
            alignment = 'BEARISH'
            strength = bearish_count / total_tf
        elif bullish_count > bearish_count:
            alignment = 'BULLISH'
            strength = bullish_count / total_tf * 0.8  # Reduce strength for mixed
        elif bearish_count > bullish_count:
            alignment = 'BEARISH'
            strength = bearish_count / total_tf * 0.8
        else:
            alignment = 'MIXED'
            strength = 0.0
        
        return {
            'alignment': alignment,
            'trend_by_tf': {k: v.value for k, v in trend_by_tf.items()},
            'momentum_by_tf': momentum_by_tf,
            'strength': strength,
            'bullish_count': bullish_count,
            'bearish_count': bearish_count
        }
    
    except Exception as e:
        return {
            'alignment': 'UNKNOWN',
            'trend_by_tf': {},
            'momentum_by_tf': {},
            'strength': 0.0,
            'error': str(e)
        }


def get_multi_timeframe_signal_strength(details: Dict) -> float:
    """
    Get signal strength from multi-timeframe analysis
    
    Args:
        details: Result from check_multi_timeframe_alignment()
    
    Returns:
        Signal strength (0-1)
    """
    if 'error' in details:
        return 0.0
    
    alignment = details.get('alignment', 'UNKNOWN')
    strength = details.get('strength', 0.0)
    
    if alignment == 'BULLISH':
        return strength
    elif alignment == 'BEARISH':
        return -strength  # Negative for bearish
    else:
        return 0.0


__all__ = [
    'TrendDirection',
    'calculate_trend_from_candles',
    'calculate_momentum_from_candles',
    'check_multi_timeframe_alignment',
    'get_multi_timeframe_signal_strength'
]

