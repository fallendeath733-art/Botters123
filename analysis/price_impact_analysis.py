"""
Price Impact Analysis Module
Menghitung price impact untuk order size tertentu sebelum execution
Mencegah slippage yang merugikan dan optimize entry/exit price
"""

from typing import Dict, Optional, Tuple
import math


def calculate_price_impact(
    order_size_usd: float,
    orderbook: Dict,
    current_price: float,
    volatility: Optional[float] = None
) -> Dict[str, any]:
    """
    Calculate price impact untuk order size tertentu
    
    Price Impact = (Order Size / Market Depth) * Spread
    Market Impact Score = Price Impact / Volatility
    
    Args:
        order_size_usd: Order size dalam USD
        orderbook: Orderbook data dengan 'bids' dan 'asks'
        current_price: Current market price
        volatility: Optional volatility untuk normalized impact score
    
    Returns:
        Dict dengan:
            - price_impact_pct: Estimated price impact dalam %
            - market_impact_score: Normalized impact score (0-1, higher = worse)
            - recommended_max_size: Maximum order size untuk impact < 0.5%
            - market_depth_usd: Available market depth
            - spread_pct: Current spread
            - impact_level: 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
            - should_reduce_size: Boolean, apakah order size perlu dikurangi
    """
    try:
        if not orderbook or 'bids' not in orderbook or 'asks' not in orderbook:
            return {
                'price_impact_pct': 999.0,
                'market_impact_score': 1.0,
                'recommended_max_size': 0.0,
                'market_depth_usd': 0.0,
                'spread_pct': 0.0,
                'impact_level': 'CRITICAL',
                'should_reduce_size': True,
                'error': 'Invalid orderbook data'
            }
        
        bids = orderbook['bids']
        asks = orderbook['asks']
        
        if not bids or not asks:
            return {
                'price_impact_pct': 999.0,
                'market_impact_score': 1.0,
                'recommended_max_size': 0.0,
                'market_depth_usd': 0.0,
                'spread_pct': 0.0,
                'impact_level': 'CRITICAL',
                'should_reduce_size': True,
                'error': 'Empty orderbook'
            }
        
        # Calculate market depth (top 10 levels for better accuracy)
        ask_depth = 0.0
        bid_depth = 0.0
        
        for ask in asks[:10]:
            try:
                price = float(ask[0])
                volume = float(ask[1])
                ask_depth += volume * price
            except (ValueError, IndexError):
                continue
        
        for bid in bids[:10]:
            try:
                price = float(bid[0])
                volume = float(bid[1])
                bid_depth += volume * price
            except (ValueError, IndexError):
                continue
        
        total_depth = ask_depth + bid_depth
        
        # Calculate spread
        best_ask = float(asks[0][0])
        best_bid = float(bids[0][0])
        spread_pct = ((best_ask - best_bid) / best_bid) * 100 if best_bid > 0 else 0
        
        # Calculate price impact
        # Impact = (Order Size / Market Depth) * Spread
        if total_depth > 0:
            impact_ratio = order_size_usd / total_depth
            price_impact_pct = impact_ratio * spread_pct * 10  # Multiply by 10 for realistic impact
        else:
            price_impact_pct = 999.0
        
        # Calculate market impact score (normalized)
        if volatility and volatility > 0:
            market_impact_score = min(1.0, price_impact_pct / (volatility * 100))
        else:
            # Default volatility assumption (2%)
            market_impact_score = min(1.0, price_impact_pct / 2.0)
        
        # Determine impact level
        if price_impact_pct < 0.1:
            impact_level = 'LOW'
        elif price_impact_pct < 0.5:
            impact_level = 'MEDIUM'
        elif price_impact_pct < 1.0:
            impact_level = 'HIGH'
        else:
            impact_level = 'CRITICAL'
        
        # Calculate recommended max size (for impact < 0.5%)
        if total_depth > 0 and spread_pct > 0:
            max_impact_pct = 0.5
            recommended_max_size = (max_impact_pct / (spread_pct * 10)) * total_depth
        else:
            recommended_max_size = 0.0
        
        should_reduce_size = price_impact_pct > 0.5 or market_impact_score > 0.5
        
        return {
            'price_impact_pct': price_impact_pct,
            'market_impact_score': market_impact_score,
            'recommended_max_size': recommended_max_size,
            'market_depth_usd': total_depth,
            'spread_pct': spread_pct,
            'impact_level': impact_level,
            'should_reduce_size': should_reduce_size,
            'order_size_usd': order_size_usd,
            'current_price': current_price
        }
    
    except Exception as e:
        return {
            'price_impact_pct': 999.0,
            'market_impact_score': 1.0,
            'recommended_max_size': 0.0,
            'market_depth_usd': 0.0,
            'spread_pct': 0.0,
            'impact_level': 'CRITICAL',
            'should_reduce_size': True,
            'error': str(e)
        }


def get_optimal_order_size(
    orderbook: Dict,
    current_price: float,
    max_impact_pct: float = 0.5,
    volatility: Optional[float] = None
) -> Dict[str, any]:
    """
    Get optimal order size untuk minimize price impact
    
    Args:
        orderbook: Orderbook data
        current_price: Current market price
        max_impact_pct: Maximum acceptable price impact (default 0.5%)
        volatility: Optional volatility
    
    Returns:
        Dict dengan:
            - optimal_size_usd: Optimal order size in USD
            - estimated_impact: Estimated price impact
            - market_depth: Available market depth
    """
    # Calculate market depth
    bids = orderbook.get('bids', [])
    asks = orderbook.get('asks', [])
    
    if not bids or not asks:
        return {
            'optimal_size_usd': 0.0,
            'estimated_impact': 999.0,
            'market_depth': 0.0,
            'error': 'Empty orderbook'
        }
    
    ask_depth = sum([float(ask[0]) * float(ask[1]) for ask in asks[:10]])
    bid_depth = sum([float(bid[0]) * float(bid[1]) for bid in bids[:10]])
    total_depth = ask_depth + bid_depth
    
    # Calculate spread
    best_ask = float(asks[0][0])
    best_bid = float(bids[0][0])
    spread_pct = ((best_ask - best_bid) / best_bid) * 100 if best_bid > 0 else 0
    
    # Calculate optimal size
    if total_depth > 0 and spread_pct > 0:
        optimal_size_usd = (max_impact_pct / (spread_pct * 10)) * total_depth
    else:
        optimal_size_usd = 0.0
    
    # Estimate impact for optimal size
    impact_result = calculate_price_impact(optimal_size_usd, orderbook, current_price, volatility)
    
    return {
        'optimal_size_usd': optimal_size_usd,
        'estimated_impact': impact_result.get('price_impact_pct', 0),
        'market_depth': total_depth,
        'spread_pct': spread_pct
    }


def should_execute_based_on_impact(
    impact_result: Dict,
    max_acceptable_impact: float = 0.5,
    max_acceptable_score: float = 0.5
) -> Tuple[bool, str]:
    """
    Determine if should execute order berdasarkan price impact
    
    Args:
        impact_result: Result from calculate_price_impact()
        max_acceptable_impact: Maximum acceptable price impact % (default 0.5%)
        max_acceptable_score: Maximum acceptable market impact score (default 0.5)
    
    Returns:
        Tuple of (should_execute, reason)
    """
    if 'error' in impact_result:
        return False, f"Impact calculation error: {impact_result['error']}"
    
    price_impact_pct = impact_result.get('price_impact_pct', 999.0)
    market_impact_score = impact_result.get('market_impact_score', 1.0)
    impact_level = impact_result.get('impact_level', 'CRITICAL')
    
    if price_impact_pct > max_acceptable_impact:
        return False, f"Price impact too high: {price_impact_pct:.2f}% > {max_acceptable_impact:.2f}% ({impact_level})"
    
    if market_impact_score > max_acceptable_score:
        return False, f"Market impact score too high: {market_impact_score:.2f} > {max_acceptable_score:.2f}"
    
    if impact_level in ['HIGH', 'CRITICAL']:
        return False, f"Impact level too high: {impact_level}"
    
    return True, f"Price impact acceptable: {price_impact_pct:.2f}% ({impact_level})"


__all__ = [
    'calculate_price_impact',
    'get_optimal_order_size',
    'should_execute_based_on_impact'
]

