"""
Helper utility functions.
"""
from typing import Optional
from datetime import datetime
import math


def normalize_symbol(symbol: str) -> str:
    """Normalize symbol format (e.g., BTC_USDT -> BTCUSDT)."""
    return symbol.replace('_', '')


def safe_division(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Safe division that handles zero division and NaN/Inf values.
    
    Args:
        numerator: Numerator value
        denominator: Denominator value
        default: Default value to return if division fails
        
    Returns:
        Result of division or default value
    """
    if denominator == 0 or (hasattr(math, 'isnan') and (math.isnan(denominator) or math.isnan(numerator))):
        return default
    try:
        result = numerator / denominator
        return result if not (hasattr(math, 'isnan') and (math.isnan(result) or math.isinf(result))) else default
    except:
        return default


def fmt(x: Optional[float]) -> str:
    """
    Format float value for display.
    
    Args:
        x: Float value to format
        
    Returns:
        Formatted string
    """
    if x is None:
        return "N/A"
    if abs(x) < 0.01:
        return f"{x:.6f}".rstrip('0').rstrip('.')
    elif abs(x) < 1:
        return f"{x:.4f}".rstrip('0').rstrip('.')
    elif abs(x) < 100:
        return f"{x:.2f}".rstrip('0').rstrip('.')
    else:
        return f"{x:.2f}"


def now_ts() -> str:
    """
    Get current timestamp as formatted string.
    
    Returns:
        Formatted timestamp string
    """
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


__all__ = ['normalize_symbol', 'safe_division', 'fmt', 'now_ts']

