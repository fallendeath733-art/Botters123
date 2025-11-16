"""
Open Interest + Long/Short Ratio Analysis Module
Menggunakan futures data untuk leverage & sentiment analysis
"""

from typing import Dict, Optional, Tuple
import time


async def get_open_interest_data(
    symbol: str,
    exchange_client=None
) -> Optional[Dict]:
    """
    Get Open Interest data dari exchange
    
    Args:
        symbol: Trading symbol (e.g., "BTC_USDT")
        exchange_client: Exchange client instance
    
    Returns:
        Dict dengan 'open_interest', 'oi_change_24h', 'timestamp', atau None jika tidak available
    """
    if exchange_client is None:
        # Lazy import untuk avoid circular dependency
        try:
            from core import _init_exchange_client
            exchange_client = _init_exchange_client()
        except ImportError:
            return None
    
    if not exchange_client:
        return None
    
    try:
        # Try to get OI from exchange client
        if hasattr(exchange_client, 'get_open_interest'):
            oi_data = await exchange_client.get_open_interest(symbol) if hasattr(exchange_client.get_open_interest, '__call__') else exchange_client.get_open_interest(symbol)
            if oi_data:
                return {
                    'open_interest': float(oi_data.get('open_interest', 0)),
                    'oi_change_24h': float(oi_data.get('oi_change_24h', 0)),
                    'timestamp': time.time()
                }
    except Exception:
        pass
    
    # Fallback: return None if not available
    return None


async def get_long_short_ratio_data(
    symbol: str,
    exchange_client=None
) -> Optional[Dict]:
    """
    Get Long/Short Ratio data dari exchange
    
    Args:
        symbol: Trading symbol (e.g., "BTC_USDT")
        exchange_client: Exchange client instance
    
    Returns:
        Dict dengan 'long_short_ratio', 'long_accounts', 'short_accounts', 'timestamp', atau None jika tidak available
    """
    if exchange_client is None:
        # Lazy import untuk avoid circular dependency
        try:
            from core import _init_exchange_client
            exchange_client = _init_exchange_client()
        except ImportError:
            return None
    
    if not exchange_client:
        return None
    
    try:
        # Try to get Long/Short ratio from exchange client
        if hasattr(exchange_client, 'get_long_short_ratio'):
            ls_data = await exchange_client.get_long_short_ratio(symbol) if hasattr(exchange_client.get_long_short_ratio, '__call__') else exchange_client.get_long_short_ratio(symbol)
            if ls_data:
                return {
                    'long_short_ratio': float(ls_data.get('long_short_ratio', 1.0)),
                    'long_accounts': float(ls_data.get('long_accounts', 0)),
                    'short_accounts': float(ls_data.get('short_accounts', 0)),
                    'timestamp': time.time()
                }
    except Exception:
        pass
    
    # Fallback: return None if not available
    return None


async def analyze_open_interest_sentiment(
    symbol: str,
    oi_data: Optional[Dict] = None,
    ls_data: Optional[Dict] = None,
    exchange_client=None
) -> Dict[str, any]:
    """
    Analyze sentiment dari Open Interest dan Long/Short Ratio
    
    Args:
        symbol: Trading symbol
        oi_data: Optional pre-fetched OI data
        ls_data: Optional pre-fetched Long/Short ratio data
        exchange_client: Optional exchange client
    
    Returns:
        Dict dengan:
            - sentiment: 'BULLISH', 'BEARISH', or 'NEUTRAL'
            - oi_sentiment: OI-based sentiment
            - ls_sentiment: Long/Short ratio-based sentiment
            - leverage_risk: Leverage risk level (0-1)
            - confidence: Confidence in sentiment (0-1)
    """
    # Get data if not provided
    if oi_data is None:
        oi_data = await get_open_interest_data(symbol, exchange_client)
    
    if ls_data is None:
        ls_data = await get_long_short_ratio_data(symbol, exchange_client)
    
    # Analyze OI sentiment
    oi_sentiment = 'NEUTRAL'
    oi_confidence = 0.0
    if oi_data:
        oi_change = oi_data.get('oi_change_24h', 0)
        if oi_change > 5.0:  # OI increased > 5%
            oi_sentiment = 'BULLISH'  # More positions opened
            oi_confidence = min(1.0, abs(oi_change) / 20.0)
        elif oi_change < -5.0:  # OI decreased > 5%
            oi_sentiment = 'BEARISH'  # Positions closed
            oi_confidence = min(1.0, abs(oi_change) / 20.0)
    
    # Analyze Long/Short ratio sentiment
    ls_sentiment = 'NEUTRAL'
    ls_confidence = 0.0
    if ls_data:
        ls_ratio = ls_data.get('long_short_ratio', 1.0)
        if ls_ratio > 1.2:  # More longs than shorts
            ls_sentiment = 'BULLISH'
            ls_confidence = min(1.0, (ls_ratio - 1.0) / 0.5)
        elif ls_ratio < 0.8:  # More shorts than longs
            ls_sentiment = 'BEARISH'
            ls_confidence = min(1.0, (1.0 - ls_ratio) / 0.5)
    
    # Calculate leverage risk
    leverage_risk = 0.0
    if oi_data and ls_data:
        oi_value = oi_data.get('open_interest', 0)
        ls_ratio = ls_data.get('long_short_ratio', 1.0)
        
        # High OI + extreme LS ratio = high leverage risk
        if oi_value > 0:
            # Normalize OI (assume max OI = 1B for normalization)
            normalized_oi = min(1.0, oi_value / 1_000_000_000)
            
            # Extreme LS ratio increases risk
            ls_extremity = abs(ls_ratio - 1.0)
            
            leverage_risk = normalized_oi * (1.0 + ls_extremity)
            leverage_risk = min(1.0, leverage_risk)
    
    # Determine overall sentiment
    bullish_signals = 0
    bearish_signals = 0
    
    if oi_sentiment == 'BULLISH':
        bullish_signals += 1
    elif oi_sentiment == 'BEARISH':
        bearish_signals += 1
    
    if ls_sentiment == 'BULLISH':
        bullish_signals += 1
    elif ls_sentiment == 'BEARISH':
        bearish_signals += 1
    
    if bullish_signals > bearish_signals:
        overall_sentiment = 'BULLISH'
        confidence = (oi_confidence + ls_confidence) / 2.0
    elif bearish_signals > bullish_signals:
        overall_sentiment = 'BEARISH'
        confidence = (oi_confidence + ls_confidence) / 2.0
    else:
        overall_sentiment = 'NEUTRAL'
        confidence = 0.0
    
    return {
        'sentiment': overall_sentiment,
        'oi_sentiment': oi_sentiment,
        'ls_sentiment': ls_sentiment,
        'leverage_risk': leverage_risk,
        'confidence': confidence,
        'oi_data': oi_data,
        'ls_data': ls_data
    }


def get_oi_ls_entry_signal(
    oi_analysis: Dict,
    min_confidence: float = 0.3
) -> Tuple[bool, str]:
    """
    Get entry signal berdasarkan Open Interest + Long/Short analysis
    
    Args:
        oi_analysis: Result from analyze_open_interest_sentiment()
        min_confidence: Minimum confidence required (0-1)
    
    Returns:
        Tuple of (should_enter, reason)
    """
    if 'error' in oi_analysis:
        return False, f"OI analysis error: {oi_analysis['error']}"
    
    sentiment = oi_analysis.get('sentiment', 'NEUTRAL')
    confidence = oi_analysis.get('confidence', 0.0)
    leverage_risk = oi_analysis.get('leverage_risk', 0.0)
    
    if sentiment == 'BEARISH':
        return False, f"Bearish OI/LS sentiment (confidence: {confidence:.2f}) - avoid entry"
    
    if confidence < min_confidence:
        return False, f"OI/LS confidence too low: {confidence:.2f} < {min_confidence:.2f}"
    
    if leverage_risk > 0.7:  # High leverage risk
        return False, f"High leverage risk: {leverage_risk:.2f} - avoid entry"
    
    if sentiment == 'BULLISH':
        return True, f"Bullish OI/LS sentiment (confidence: {confidence:.2f}, risk: {leverage_risk:.2f})"
    else:
        return False, f"Neutral OI/LS sentiment (confidence: {confidence:.2f})"


def get_oi_ls_exit_signal(
    oi_analysis: Dict,
    entry_price: float,
    current_price: float
) -> Tuple[bool, str]:
    """
    Get exit signal berdasarkan Open Interest + Long/Short analysis
    
    Args:
        oi_analysis: Result from analyze_open_interest_sentiment()
        entry_price: Entry price
        current_price: Current price
    
    Returns:
        Tuple of (should_exit, reason)
    """
    if 'error' in oi_analysis:
        return False, "OI analysis not available"
    
    sentiment = oi_analysis.get('sentiment', 'NEUTRAL')
    leverage_risk = oi_analysis.get('leverage_risk', 0.0)
    profit_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
    
    # Exit if sentiment turns bearish
    if sentiment == 'BEARISH' and profit_pct > 0:
        return True, f"OI/LS sentiment turned bearish (profit: {profit_pct:.2f}%)"
    
    # Exit if leverage risk becomes too high
    if leverage_risk > 0.8 and profit_pct > 1.0:
        return True, f"Leverage risk too high: {leverage_risk:.2f} (profit: {profit_pct:.2f}%)"
    
    return False, f"No exit signal (sentiment: {sentiment}, risk: {leverage_risk:.2f})"


def calculate_leverage_risk(
    oi_value: float,
    ls_ratio: float,
    price_volatility: float = 0.02
) -> float:
    """
    Calculate leverage risk based on OI, LS ratio, and volatility
    
    Args:
        oi_value: Open Interest value
        ls_ratio: Long/Short ratio
        price_volatility: Price volatility (default 2%)
    
    Returns:
        Leverage risk (0-1, where 1 = maximum risk)
    """
    # Normalize OI (assume max OI = 1B)
    normalized_oi = min(1.0, oi_value / 1_000_000_000)
    
    # Extreme LS ratio increases risk
    ls_extremity = abs(ls_ratio - 1.0)
    
    # Volatility increases risk
    volatility_factor = min(2.0, price_volatility / 0.01)  # 1% = 1.0, 2% = 2.0
    
    # Calculate risk
    risk = normalized_oi * (1.0 + ls_extremity) * volatility_factor
    return min(1.0, risk)


__all__ = [
    'get_open_interest_data',
    'get_long_short_ratio_data',
    'analyze_open_interest_sentiment',
    'get_oi_ls_entry_signal',
    'get_oi_ls_exit_signal',
    'calculate_leverage_risk'
]

