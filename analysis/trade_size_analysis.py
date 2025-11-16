"""
Trade Size Analysis Module
Analisa trade size untuk detect institutional activity dan smart money
"""

from typing import Dict, List, Optional, Tuple
import statistics


def calculate_trade_size_distribution(trades: List[Dict]) -> Dict[str, any]:
    """
    Calculate trade size distribution untuk detect institutional activity
    
    Args:
        trades: List of trade dicts dengan 'size' atau 'amount' dan 'price'
    
    Returns:
        Dict dengan distribution statistics
    """
    if not trades or len(trades) < 10:
        return {
            'mean': 0.0,
            'median': 0.0,
            'std_dev': 0.0,
            'institutional_threshold': 0.0,
            'large_trades_count': 0,
            'large_trades_pct': 0.0,
            'error': 'Insufficient trades'
        }
    
    try:
        # Extract trade sizes (in USD)
        trade_sizes = []
        for trade in trades:
            size = trade.get('size', trade.get('amount', 0))
            price = trade.get('price', 0)
            if size > 0 and price > 0:
                trade_sizes.append(size * price)
        
        if len(trade_sizes) < 10:
            return {
                'mean': 0.0,
                'median': 0.0,
                'std_dev': 0.0,
                'institutional_threshold': 0.0,
                'large_trades_count': 0,
                'large_trades_pct': 0.0,
                'error': 'Insufficient valid trades'
            }
        
        # Calculate statistics
        mean = statistics.mean(trade_sizes)
        median = statistics.median(trade_sizes)
        std_dev = statistics.stdev(trade_sizes) if len(trade_sizes) > 1 else 0.0
        
        # Institutional threshold = mean + 2*std_dev
        institutional_threshold = mean + (2 * std_dev)
        
        # Count large trades
        large_trades_count = sum(1 for size in trade_sizes if size >= institutional_threshold)
        large_trades_pct = (large_trades_count / len(trade_sizes)) * 100 if trade_sizes else 0.0
        
        return {
            'mean': mean,
            'median': median,
            'std_dev': std_dev,
            'institutional_threshold': institutional_threshold,
            'large_trades_count': large_trades_count,
            'large_trades_pct': large_trades_pct,
            'total_trades': len(trade_sizes),
            'min_size': min(trade_sizes),
            'max_size': max(trade_sizes)
        }
    
    except Exception as e:
        return {
            'mean': 0.0,
            'median': 0.0,
            'std_dev': 0.0,
            'institutional_threshold': 0.0,
            'large_trades_count': 0,
            'large_trades_pct': 0.0,
            'error': str(e)
        }


def get_dynamic_institutional_threshold(
    distribution: Dict,
    multiplier: float = 2.0
) -> float:
    """
    Get dynamic institutional threshold berdasarkan distribution
    
    Args:
        distribution: Result from calculate_trade_size_distribution()
        multiplier: Multiplier for standard deviation (default 2.0)
    
    Returns:
        Institutional threshold in USD
    """
    if 'error' in distribution:
        return 10000.0  # Default threshold
    
    mean = distribution.get('mean', 0)
    std_dev = distribution.get('std_dev', 0)
    
    threshold = mean + (multiplier * std_dev)
    return max(1000.0, threshold)  # Minimum $1000


def detect_institutional_activity(
    trades: List[Dict],
    threshold: Optional[float] = None
) -> Dict[str, any]:
    """
    Detect institutional activity dari trade sizes
    
    Args:
        trades: List of trade dicts
        threshold: Optional threshold (will calculate if not provided)
    
    Returns:
        Dict dengan institutional activity analysis
    """
    distribution = calculate_trade_size_distribution(trades)
    
    if 'error' in distribution:
        return {
            'has_institutional': False,
            'activity_level': 'LOW',
            'large_trades_count': 0,
            'error': distribution['error']
        }
    
    if threshold is None:
        threshold = get_dynamic_institutional_threshold(distribution)
    
    # Count large trades
    large_trades = []
    for trade in trades:
        size = trade.get('size', trade.get('amount', 0))
        price = trade.get('price', 0)
        if size > 0 and price > 0:
            trade_size_usd = size * price
            if trade_size_usd >= threshold:
                large_trades.append(trade)
    
    large_trades_count = len(large_trades)
    total_trades = len(trades)
    large_trades_pct = (large_trades_count / total_trades) * 100 if total_trades > 0 else 0.0
    
    # Determine activity level
    if large_trades_pct > 10.0:  # > 10% large trades
        activity_level = 'HIGH'
        has_institutional = True
    elif large_trades_pct > 5.0:  # > 5% large trades
        activity_level = 'MEDIUM'
        has_institutional = True
    elif large_trades_pct > 1.0:  # > 1% large trades
        activity_level = 'LOW'
        has_institutional = True
    else:
        activity_level = 'NONE'
        has_institutional = False
    
    return {
        'has_institutional': has_institutional,
        'activity_level': activity_level,
        'large_trades_count': large_trades_count,
        'large_trades_pct': large_trades_pct,
        'threshold': threshold,
        'distribution': distribution
    }


def analyze_trade_size_trend(
    trades: List[Dict],
    window_size: int = 50
) -> Dict[str, any]:
    """
    Analyze trend dalam trade sizes (increasing/decreasing)
    
    Args:
        trades: List of trade dicts
        window_size: Window size for trend analysis
    
    Returns:
        Dict dengan trend analysis
    """
    if not trades or len(trades) < window_size:
        return {
            'trend': 'UNKNOWN',
            'trend_strength': 0.0,
            'error': 'Insufficient trades'
        }
    
    try:
        # Get recent trades
        recent_trades = trades[-window_size:]
        
        # Calculate average size for first half and second half
        mid_point = len(recent_trades) // 2
        first_half = recent_trades[:mid_point]
        second_half = recent_trades[mid_point:]
        
        def avg_size(trade_list):
            sizes = []
            for trade in trade_list:
                size = trade.get('size', trade.get('amount', 0))
                price = trade.get('price', 0)
                if size > 0 and price > 0:
                    sizes.append(size * price)
            return statistics.mean(sizes) if sizes else 0.0
        
        avg_first = avg_size(first_half)
        avg_second = avg_size(second_half)
        
        # Determine trend
        if avg_second > avg_first * 1.2:  # 20% increase
            trend = 'INCREASING'
            trend_strength = min(1.0, (avg_second - avg_first) / avg_first)
        elif avg_second < avg_first * 0.8:  # 20% decrease
            trend = 'DECREASING'
            trend_strength = min(1.0, (avg_first - avg_second) / avg_first)
        else:
            trend = 'STABLE'
            trend_strength = 0.0
        
        return {
            'trend': trend,
            'trend_strength': trend_strength,
            'avg_first_half': avg_first,
            'avg_second_half': avg_second,
            'change_pct': ((avg_second - avg_first) / avg_first) * 100 if avg_first > 0 else 0
        }
    
    except Exception as e:
        return {
            'trend': 'UNKNOWN',
            'trend_strength': 0.0,
            'error': str(e)
        }


def get_trade_size_entry_signal(
    institutional_activity: Dict,
    trade_trend: Dict,
    min_activity_level: str = 'LOW'
) -> Tuple[bool, str]:
    """
    Get entry signal berdasarkan trade size analysis
    
    Args:
        institutional_activity: Result from detect_institutional_activity()
        trade_trend: Result from analyze_trade_size_trend()
        min_activity_level: Minimum activity level required
    
    Returns:
        Tuple of (should_enter, reason)
    """
    if 'error' in institutional_activity:
        return False, f"Institutional analysis error: {institutional_activity['error']}"
    
    has_institutional = institutional_activity.get('has_institutional', False)
    activity_level = institutional_activity.get('activity_level', 'NONE')
    
    # Activity level hierarchy
    level_hierarchy = {'NONE': 0, 'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}
    min_level = level_hierarchy.get(min_activity_level, 0)
    current_level = level_hierarchy.get(activity_level, 0)
    
    if current_level < min_level:
        return False, f"Institutional activity too low: {activity_level} < {min_activity_level}"
    
    # Check trend
    trend = trade_trend.get('trend', 'UNKNOWN')
    if trend == 'DECREASING':
        return False, "Trade size trend decreasing - institutional selling"
    
    if has_institutional and activity_level in ['MEDIUM', 'HIGH']:
        trend_info = f" ({trend})" if trend != 'UNKNOWN' else ""
        return True, f"Institutional activity detected: {activity_level}{trend_info}"
    elif has_institutional:
        return True, f"Low institutional activity: {activity_level}"
    else:
        return False, "No institutional activity detected"


__all__ = [
    'calculate_trade_size_distribution',
    'get_dynamic_institutional_threshold',
    'detect_institutional_activity',
    'analyze_trade_size_trend',
    'get_trade_size_entry_signal'
]

