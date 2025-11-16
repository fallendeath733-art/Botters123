"""
Divergence Analysis Module
Detect bullish/bearish divergence antara price dan indicators (RSI, MACD, Volume)
"""

from typing import Dict, Optional, Tuple, List
from enum import Enum


class DivergenceType(Enum):
    """Type of divergence detected"""
    BULLISH = "BULLISH"  # Price Lower Low, Indicator Higher Low
    BEARISH = "BEARISH"  # Price Higher High, Indicator Lower High
    NONE = "NONE"


def find_swing_points(prices: List[float], lookback: int = 5) -> Tuple[List[Tuple[int, float]], List[Tuple[int, float]]]:
    """
    Find swing highs and swing lows dalam price data
    
    Args:
        prices: List of prices
        lookback: Number of candles to look back/forward for swing point
    
    Returns:
        Tuple of (swing_highs, swing_lows)
        Each is a list of (index, price) tuples
    """
    if len(prices) < lookback * 2 + 1:
        return [], []
    
    swing_highs = []
    swing_lows = []
    
    for i in range(lookback, len(prices) - lookback):
        price = prices[i]
        
        # Check if it's a swing high
        is_swing_high = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and prices[j] >= price:
                is_swing_high = False
                break
        
        if is_swing_high:
            swing_highs.append((i, price))
        
        # Check if it's a swing low
        is_swing_low = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and prices[j] <= price:
                is_swing_low = False
                break
        
        if is_swing_low:
            swing_lows.append((i, price))
    
    return swing_highs, swing_lows


def detect_price_indicator_divergence(
    prices: List[float],
    indicator_values: List[float],
    min_swings: int = 2
) -> Tuple[DivergenceType, float, Dict]:
    """
    Detect divergence antara price dan indicator
    
    Args:
        prices: List of prices
        indicator_values: List of indicator values (RSI, MACD, etc.)
        min_swings: Minimum number of swings needed to detect divergence
    
    Returns:
        Tuple of (divergence_type, strength, details)
        - divergence_type: BULLISH, BEARISH, or NONE
        - strength: 0.0 to 1.0 (confidence in divergence)
        - details: Dict with divergence information
    """
    if len(prices) < 14 or len(indicator_values) < 14:
        return DivergenceType.NONE, 0.0, {'error': 'Insufficient data'}
    
    if len(prices) != len(indicator_values):
        return DivergenceType.NONE, 0.0, {'error': 'Price and indicator length mismatch'}
    
    try:
        # Find swing points in prices
        price_highs, price_lows = find_swing_points(prices, lookback=3)
        
        if len(price_highs) < min_swings and len(price_lows) < min_swings:
            return DivergenceType.NONE, 0.0, {'reason': 'Insufficient swing points'}
        
        # Get corresponding indicator values at swing points
        indicator_at_highs = [(idx, indicator_values[idx]) for idx, _ in price_highs]
        indicator_at_lows = [(idx, indicator_values[idx]) for idx, _ in price_lows]
        
        # Check for bearish divergence (Price Higher High, Indicator Lower High)
        bearish_divergence = False
        bearish_strength = 0.0
        if len(price_highs) >= 2 and len(indicator_at_highs) >= 2:
            # Compare last two swing highs
            last_high_idx, last_high_price = price_highs[-1]
            prev_high_idx, prev_high_price = price_highs[-2]
            last_high_indicator = indicator_at_highs[-1][1]
            prev_high_indicator = indicator_at_highs[-2][1]
            
            # Bearish: Price Higher High, Indicator Lower High
            if last_high_price > prev_high_price and last_high_indicator < prev_high_indicator:
                bearish_divergence = True
                price_change_pct = abs((last_high_price - prev_high_price) / prev_high_price) if prev_high_price > 0 else 0
                indicator_change_pct = abs((prev_high_indicator - last_high_indicator) / prev_high_indicator) if prev_high_indicator != 0 else 0
                bearish_strength = min(1.0, (price_change_pct + indicator_change_pct) / 2.0)
        
        # Check for bullish divergence (Price Lower Low, Indicator Higher Low)
        bullish_divergence = False
        bullish_strength = 0.0
        if len(price_lows) >= 2 and len(indicator_at_lows) >= 2:
            # Compare last two swing lows
            last_low_idx, last_low_price = price_lows[-1]
            prev_low_idx, prev_low_price = price_lows[-2]
            last_low_indicator = indicator_at_lows[-1][1]
            prev_low_indicator = indicator_at_lows[-2][1]
            
            # Bullish: Price Lower Low, Indicator Higher Low
            if last_low_price < prev_low_price and last_low_indicator > prev_low_indicator:
                bullish_divergence = True
                price_change_pct = abs((prev_low_price - last_low_price) / prev_low_price) if prev_low_price > 0 else 0
                indicator_change_pct = abs((last_low_indicator - prev_low_indicator) / prev_low_indicator) if prev_low_indicator != 0 else 0
                bullish_strength = min(1.0, (price_change_pct + indicator_change_pct) / 2.0)
        
        # Determine final divergence type and strength
        if bullish_divergence and bullish_strength > bearish_strength:
            return DivergenceType.BULLISH, bullish_strength, {
                'type': 'BULLISH',
                'strength': bullish_strength,
                'price_lows': price_lows[-2:],
                'indicator_lows': indicator_at_lows[-2:],
                'reason': 'Price Lower Low, Indicator Higher Low'
            }
        elif bearish_divergence and bearish_strength > bullish_strength:
            return DivergenceType.BEARISH, bearish_strength, {
                'type': 'BEARISH',
                'strength': bearish_strength,
                'price_highs': price_highs[-2:],
                'indicator_highs': indicator_at_highs[-2:],
                'reason': 'Price Higher High, Indicator Lower High'
            }
        else:
            return DivergenceType.NONE, 0.0, {'reason': 'No clear divergence detected'}
    
    except Exception as e:
        return DivergenceType.NONE, 0.0, {'error': str(e)}


async def analyze_divergence(
    candles: List[Dict],
    rsi_values: Optional[List[float]] = None,
    macd_values: Optional[List[float]] = None,
    volume_values: Optional[List[float]] = None
) -> Dict[str, any]:
    """
    Analyze divergence untuk multiple indicators
    
    Args:
        candles: List of candle data dengan 'close', 'high', 'low', 'volume'
        rsi_values: Optional pre-calculated RSI values
        macd_values: Optional pre-calculated MACD values
        volume_values: Optional volume values
    
    Returns:
        Dict dengan:
            - divergence_type: BULLISH, BEARISH, or NONE
            - strength: Overall strength (0-1)
            - rsi_divergence: RSI divergence details
            - macd_divergence: MACD divergence details
            - volume_divergence: Volume divergence details
            - overall_signal: 'BULLISH', 'BEARISH', or 'NEUTRAL'
    """
    if not candles or len(candles) < 20:
        return {
            'divergence_type': DivergenceType.NONE,
            'strength': 0.0,
            'overall_signal': 'NEUTRAL',
            'error': 'Insufficient candles'
        }
    
    try:
        # Extract prices
        prices = [float(c.get('close', 0)) for c in candles]
        
        # Calculate RSI if not provided
        if rsi_values is None:
            rsi_values = calculate_rsi_series(prices, period=14)
        
        # Calculate MACD if not provided (simplified)
        if macd_values is None:
            macd_values = calculate_macd_series(prices)
        
        # Get volume if available
        if volume_values is None:
            volume_values = [float(c.get('volume', 0)) for c in candles]
        
        # Analyze divergence for each indicator
        rsi_div_type, rsi_strength, rsi_details = detect_price_indicator_divergence(prices, rsi_values)
        macd_div_type, macd_strength, macd_details = detect_price_indicator_divergence(prices, macd_values)
        volume_div_type, volume_strength, volume_details = detect_price_indicator_divergence(prices, volume_values)
        
        # Determine overall divergence
        bullish_signals = 0
        bearish_signals = 0
        total_strength = 0.0
        
        if rsi_div_type == DivergenceType.BULLISH:
            bullish_signals += 1
            total_strength += rsi_strength
        elif rsi_div_type == DivergenceType.BEARISH:
            bearish_signals += 1
            total_strength += rsi_strength
        
        if macd_div_type == DivergenceType.BULLISH:
            bullish_signals += 1
            total_strength += macd_strength
        elif macd_div_type == DivergenceType.BEARISH:
            bearish_signals += 1
            total_strength += macd_strength
        
        if volume_div_type == DivergenceType.BULLISH:
            bullish_signals += 1
            total_strength += volume_strength
        elif volume_div_type == DivergenceType.BEARISH:
            bearish_signals += 1
            total_strength += volume_strength
        
        # Calculate average strength
        signal_count = bullish_signals + bearish_signals
        avg_strength = total_strength / signal_count if signal_count > 0 else 0.0
        
        # Determine overall divergence type
        if bullish_signals > bearish_signals:
            overall_type = DivergenceType.BULLISH
            overall_signal = 'BULLISH'
        elif bearish_signals > bullish_signals:
            overall_type = DivergenceType.BEARISH
            overall_signal = 'BEARISH'
        else:
            overall_type = DivergenceType.NONE
            overall_signal = 'NEUTRAL'
        
        return {
            'divergence_type': overall_type,
            'strength': avg_strength,
            'rsi_divergence': {
                'type': rsi_div_type.value,
                'strength': rsi_strength,
                'details': rsi_details
            },
            'macd_divergence': {
                'type': macd_div_type.value,
                'strength': macd_strength,
                'details': macd_details
            },
            'volume_divergence': {
                'type': volume_div_type.value,
                'strength': volume_strength,
                'details': volume_details
            },
            'overall_signal': overall_signal,
            'bullish_signals': bullish_signals,
            'bearish_signals': bearish_signals
        }
    
    except Exception as e:
        return {
            'divergence_type': DivergenceType.NONE,
            'strength': 0.0,
            'overall_signal': 'NEUTRAL',
            'error': str(e)
        }


def calculate_rsi_series(prices: List[float], period: int = 14) -> List[float]:
    """
    Calculate RSI series from prices
    
    Args:
        prices: List of prices
        period: RSI period (default 14)
    
    Returns:
        List of RSI values
    """
    if len(prices) < period + 1:
        return [50.0] * len(prices)  # Default neutral RSI
    
    rsi_values = []
    gains = []
    losses = []
    
    # Calculate initial average gain and loss
    for i in range(1, period + 1):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))
    
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    
    # Calculate RSI for initial period
    for i in range(period):
        rsi_values.append(50.0)  # Default for first period
    
    # Calculate RSI for remaining periods
    for i in range(period, len(prices)):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gain = change
            loss = 0.0
        else:
            gain = 0.0
            loss = abs(change)
        
        # Use Welles Wilder's smoothing method
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))
        
        rsi_values.append(rsi)
    
    return rsi_values


def calculate_macd_series(prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> List[float]:
    """
    Calculate MACD series from prices (simplified - returns MACD line only)
    
    Args:
        prices: List of prices
        fast: Fast EMA period (default 12)
        slow: Slow EMA period (default 26)
        signal: Signal line period (default 9, not used in this simplified version)
    
    Returns:
        List of MACD values
    """
    if len(prices) < slow:
        return [0.0] * len(prices)
    
    # Calculate EMAs
    fast_ema = []
    slow_ema = []
    
    # Initialize with SMA
    fast_sma = sum(prices[:fast]) / fast
    slow_sma = sum(prices[:slow]) / slow
    
    for i in range(fast):
        fast_ema.append(fast_sma)
    
    for i in range(slow):
        slow_ema.append(slow_sma)
    
    # Calculate EMAs
    alpha_fast = 2.0 / (fast + 1)
    alpha_slow = 2.0 / (slow + 1)
    
    for i in range(fast, len(prices)):
        fast_ema.append(alpha_fast * prices[i] + (1 - alpha_fast) * fast_ema[-1])
    
    for i in range(slow, len(prices)):
        slow_ema.append(alpha_slow * prices[i] + (1 - alpha_slow) * slow_ema[-1])
    
    # Calculate MACD line
    macd_values = []
    for i in range(slow):
        macd_values.append(0.0)
    
    for i in range(slow, len(prices)):
        macd = fast_ema[i] - slow_ema[i]
        macd_values.append(macd)
    
    return macd_values


def get_divergence_signal_strength(divergence_type: DivergenceType, strength: float) -> float:
    """
    Get signal strength for divergence (0-1, where 1 = strongest signal)
    
    Args:
        divergence_type: Type of divergence
        strength: Raw strength from divergence detection
    
    Returns:
        Normalized signal strength (0-1)
    """
    if divergence_type == DivergenceType.NONE:
        return 0.0
    
    # Normalize strength to 0-1 range
    # Strength > 0.5 is considered strong
    if strength >= 0.5:
        return min(1.0, strength * 1.2)  # Boost strong signals
    else:
        return strength * 0.8  # Reduce weak signals


def should_enter_based_on_divergence(
    divergence_result: Dict,
    min_strength: float = 0.4,
    require_bullish: bool = True
) -> Tuple[bool, str]:
    """
    Determine if should enter based on divergence result
    
    Args:
        divergence_result: Result from analyze_divergence()
        min_strength: Minimum strength required (0-1)
        require_bullish: If True, only enter on bullish divergence
    
    Returns:
        Tuple of (should_enter, reason)
    """
    if 'error' in divergence_result:
        return False, f"Error: {divergence_result['error']}"
    
    divergence_type = divergence_result.get('divergence_type')
    strength = divergence_result.get('strength', 0.0)
    
    if divergence_type == DivergenceType.NONE:
        return False, "No divergence detected"
    
    if strength < min_strength:
        return False, f"Divergence strength too low: {strength:.2f} < {min_strength:.2f}"
    
    if require_bullish and divergence_type != DivergenceType.BULLISH:
        return False, f"Require bullish divergence but got {divergence_type.value}"
    
    if divergence_type == DivergenceType.BULLISH:
        return True, f"Bullish divergence detected with strength {strength:.2f}"
    elif divergence_type == DivergenceType.BEARISH:
        return False, f"Bearish divergence detected - avoid entry"
    else:
        return False, "Unknown divergence type"


__all__ = [
    'DivergenceType',
    'find_swing_points',
    'detect_price_indicator_divergence',
    'analyze_divergence',
    'calculate_rsi_series',
    'calculate_macd_series',
    'get_divergence_signal_strength',
    'should_enter_based_on_divergence'
]

