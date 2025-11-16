"""
Bollinger Bands + ADX Analysis Module
Kombinasi Bollinger Bands untuk volatility dan ADX untuk trend strength
"""

from typing import Dict, List, Optional, Tuple
import math


def calculate_bollinger_bands(
    prices: List[float],
    period: int = 20,
    std_dev: float = 2.0
) -> Dict[str, float]:
    """
    Calculate Bollinger Bands
    
    Args:
        prices: List of prices
        period: Period for SMA (default 20)
        std_dev: Standard deviation multiplier (default 2.0)
    
    Returns:
        Dict dengan 'upper', 'middle', 'lower', 'bandwidth'
    """
    if len(prices) < period:
        return {
            'upper': prices[-1] if prices else 0,
            'middle': prices[-1] if prices else 0,
            'lower': prices[-1] if prices else 0,
            'bandwidth': 0.0
        }
    
    # Calculate SMA (middle band)
    recent_prices = prices[-period:]
    sma = sum(recent_prices) / len(recent_prices)
    
    # Calculate standard deviation
    variance = sum((p - sma) ** 2 for p in recent_prices) / len(recent_prices)
    std = math.sqrt(variance)
    
    # Calculate bands
    upper = sma + (std_dev * std)
    lower = sma - (std_dev * std)
    bandwidth = ((upper - lower) / sma) * 100 if sma > 0 else 0.0
    
    return {
        'upper': upper,
        'middle': sma,
        'lower': lower,
        'bandwidth': bandwidth
    }


def calculate_adx(
    candles: List[Dict],
    period: int = 14
) -> Dict[str, float]:
    """
    Calculate ADX (Average Directional Index) untuk trend strength
    
    Args:
        candles: List of candle dicts dengan 'high', 'low', 'close'
        period: ADX period (default 14)
    
    Returns:
        Dict dengan 'adx', 'plus_di', 'minus_di'
    """
    if len(candles) < period + 1:
        return {
            'adx': 25.0,  # Default neutral
            'plus_di': 0.0,
            'minus_di': 0.0
        }
    
    try:
        # Calculate True Range (TR) and Directional Movement
        tr_values = []
        plus_dm_values = []
        minus_dm_values = []
        
        for i in range(1, len(candles)):
            high = float(candles[i].get('high', 0))
            low = float(candles[i].get('low', 0))
            prev_high = float(candles[i-1].get('high', 0))
            prev_low = float(candles[i-1].get('low', 0))
            prev_close = float(candles[i-1].get('close', 0))
            
            # True Range
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            tr_values.append(tr)
            
            # Directional Movement
            plus_dm = high - prev_high if high > prev_high and (high - prev_high) > (prev_low - low) else 0
            minus_dm = prev_low - low if prev_low > low and (prev_low - low) > (high - prev_high) else 0
            
            plus_dm_values.append(plus_dm)
            minus_dm_values.append(minus_dm)
        
        if len(tr_values) < period:
            return {
                'adx': 25.0,
                'plus_di': 0.0,
                'minus_di': 0.0
            }
        
        # Calculate smoothed TR, +DM, -DM
        atr = sum(tr_values[-period:]) / period
        plus_dm_smooth = sum(plus_dm_values[-period:]) / period
        minus_dm_smooth = sum(minus_dm_values[-period:]) / period
        
        # Calculate DI+ and DI-
        plus_di = (plus_dm_smooth / atr) * 100 if atr > 0 else 0
        minus_di = (minus_dm_smooth / atr) * 100 if atr > 0 else 0
        
        # Calculate DX
        di_sum = plus_di + minus_di
        dx = abs(plus_di - minus_di) / di_sum * 100 if di_sum > 0 else 0
        
        # ADX is smoothed DX (simplified - using current DX as ADX)
        adx = dx
        
        return {
            'adx': adx,
            'plus_di': plus_di,
            'minus_di': minus_di
        }
    
    except Exception:
        return {
            'adx': 25.0,
            'plus_di': 0.0,
            'minus_di': 0.0
        }


async def analyze_bollinger_adx(
    candles: List[Dict],
    current_price: Optional[float] = None
) -> Dict[str, any]:
    """
    Analyze kombinasi Bollinger Bands dan ADX
    
    Args:
        candles: List of candle dicts
        current_price: Optional current price
    
    Returns:
        Dict dengan analysis results
    """
    if not candles or len(candles) < 20:
        return {
            'signal': 'NEUTRAL',
            'strength': 0.0,
            'error': 'Insufficient candles'
        }
    
    try:
        # Get prices
        prices = [float(c.get('close', 0)) for c in candles if c.get('close')]
        if not current_price:
            current_price = prices[-1] if prices else 0
        
        # Calculate Bollinger Bands
        bb = calculate_bollinger_bands(prices, period=20, std_dev=2.0)
        
        # Calculate ADX
        adx_data = calculate_adx(candles, period=14)
        
        # Analyze position relative to Bollinger Bands
        upper = bb['upper']
        middle = bb['middle']
        lower = bb['lower']
        bandwidth = bb['bandwidth']
        
        # Determine BB signal
        bb_signal = 'NEUTRAL'
        if current_price <= lower:
            bb_signal = 'OVERSOLD'  # Potential buy
        elif current_price >= upper:
            bb_signal = 'OVERBOUGHT'  # Potential sell
        elif current_price > middle:
            bb_signal = 'BULLISH'
        elif current_price < middle:
            bb_signal = 'BEARISH'
        
        # Analyze ADX
        adx = adx_data['adx']
        plus_di = adx_data['plus_di']
        minus_di = adx_data['minus_di']
        
        # ADX > 25 = strong trend
        trend_strength = 'STRONG' if adx > 25 else 'WEAK'
        trend_direction = 'BULLISH' if plus_di > minus_di else 'BEARISH'
        
        # Combined signal
        signal = 'NEUTRAL'
        strength = 0.0
        
        # Strong trend + oversold = buy signal
        if bb_signal == 'OVERSOLD' and trend_strength == 'STRONG' and trend_direction == 'BULLISH':
            signal = 'BUY'
            strength = min(1.0, (adx / 50.0) * (1.0 - (current_price - lower) / (upper - lower)))
        # Strong trend + overbought = sell signal
        elif bb_signal == 'OVERBOUGHT' and trend_strength == 'STRONG' and trend_direction == 'BEARISH':
            signal = 'SELL'
            strength = min(1.0, (adx / 50.0) * ((current_price - lower) / (upper - lower)))
        # Weak trend = neutral
        elif trend_strength == 'WEAK':
            signal = 'NEUTRAL'
            strength = 0.0
        # Bullish trend + price above middle = buy
        elif bb_signal == 'BULLISH' and trend_direction == 'BULLISH':
            signal = 'BUY'
            strength = min(0.7, adx / 50.0)
        # Bearish trend + price below middle = sell
        elif bb_signal == 'BEARISH' and trend_direction == 'BEARISH':
            signal = 'SELL'
            strength = min(0.7, adx / 50.0)
        
        return {
            'signal': signal,
            'strength': strength,
            'bb_signal': bb_signal,
            'bb_upper': upper,
            'bb_middle': middle,
            'bb_lower': lower,
            'bb_bandwidth': bandwidth,
            'adx': adx,
            'plus_di': plus_di,
            'minus_di': minus_di,
            'trend_strength': trend_strength,
            'trend_direction': trend_direction,
            'current_price': current_price
        }
    
    except Exception as e:
        return {
            'signal': 'NEUTRAL',
            'strength': 0.0,
            'error': str(e)
        }


def get_bollinger_adx_entry_signal(
    analysis: Dict,
    min_strength: float = 0.3
) -> Tuple[bool, str]:
    """
    Get entry signal berdasarkan Bollinger + ADX analysis
    
    Args:
        analysis: Result from analyze_bollinger_adx()
        min_strength: Minimum strength required
    
    Returns:
        Tuple of (should_enter, reason)
    """
    if 'error' in analysis:
        return False, f"Analysis error: {analysis['error']}"
    
    signal = analysis.get('signal', 'NEUTRAL')
    strength = analysis.get('strength', 0.0)
    
    if signal == 'SELL':
        return False, "Bearish signal - avoid entry"
    
    if strength < min_strength:
        return False, f"Signal strength too low: {strength:.2f} < {min_strength:.2f}"
    
    if signal == 'BUY':
        return True, f"Bullish signal (strength: {strength:.2f})"
    else:
        return False, f"Neutral signal (strength: {strength:.2f})"


def get_bollinger_adx_exit_signal(
    analysis: Dict,
    entry_price: float
) -> Tuple[bool, str]:
    """
    Get exit signal berdasarkan Bollinger + ADX analysis
    
    Args:
        analysis: Result from analyze_bollinger_adx()
        entry_price: Entry price
    
    Returns:
        Tuple of (should_exit, reason)
    """
    if 'error' in analysis:
        return False, "Analysis not available"
    
    signal = analysis.get('signal', 'NEUTRAL')
    current_price = analysis.get('current_price', 0)
    profit_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
    
    if signal == 'SELL' and profit_pct > 0:
        return True, f"Bearish signal detected (profit: {profit_pct:.2f}%)"
    
    if profit_pct > 5.0:  # Take profit at 5%
        return True, f"Profit target reached: {profit_pct:.2f}%"
    
    return False, f"No exit signal (signal: {signal}, profit: {profit_pct:.2f}%)"


__all__ = [
    'calculate_bollinger_bands',
    'calculate_adx',
    'analyze_bollinger_adx',
    'get_bollinger_adx_entry_signal',
    'get_bollinger_adx_exit_signal'
]

