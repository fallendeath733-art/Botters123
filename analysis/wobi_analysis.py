"""
Weighted Order Book Imbalance (WOBI) Analysis Module
WOBI memberikan weight lebih besar untuk orders dekat dengan current price
Lebih akurat dari basic BIR (Bid/Ask Ratio) untuk detect manipulation dan hidden orders
"""

from typing import Dict, Optional, Tuple
import math


def calculate_weighted_orderbook_imbalance(
    orderbook: Dict,
    levels: int = 20,
    current_price: Optional[float] = None
) -> Dict[str, any]:
    """
    Calculate Weighted Order Book Imbalance (WOBI)
    
    WOBI memberikan weight lebih besar untuk orders dekat dengan current price
    Weight = 1 / (distance_from_price + 1)
    
    Args:
        orderbook: Orderbook data dengan 'bids' dan 'asks'
        levels: Number of levels to analyze (default 20)
        current_price: Optional current price (will calculate from orderbook if not provided)
    
    Returns:
        Dict dengan:
            - wobi: Weighted imbalance ratio (0-1, where 0.5 = balanced)
            - bid_weight: Total weighted bid depth
            - ask_weight: Total weighted ask depth
            - imbalance_strength: Strength of imbalance (0-1, where 1 = maximum imbalance)
            - signal: 'BULLISH', 'BEARISH', or 'NEUTRAL'
            - basic_bir: Basic Bid/Ask Ratio for comparison
            - wobi_advantage: How much WOBI differs from basic BIR (shows hidden orders)
    """
    try:
        if not orderbook or 'bids' not in orderbook or 'asks' not in orderbook:
            return {
                'wobi': 0.5,
                'bid_weight': 0.0,
                'ask_weight': 0.0,
                'imbalance_strength': 0.0,
                'signal': 'NEUTRAL',
                'basic_bir': 0.5,
                'wobi_advantage': 0.0,
                'error': 'Invalid orderbook data'
            }
        
        bids = orderbook['bids']
        asks = orderbook['asks']
        
        if not bids or not asks:
            return {
                'wobi': 0.5,
                'bid_weight': 0.0,
                'ask_weight': 0.0,
                'imbalance_strength': 0.0,
                'signal': 'NEUTRAL',
                'basic_bir': 0.5,
                'wobi_advantage': 0.0,
                'error': 'Empty orderbook'
            }
        
        # Calculate mid price if not provided
        best_ask = float(asks[0][0])
        best_bid = float(bids[0][0])
        
        if current_price is None:
            mid_price = (best_ask + best_bid) / 2
        else:
            mid_price = current_price
        
        # Calculate basic BIR for comparison
        bid_depth_basic = sum([float(bid[1]) * float(bid[0]) for bid in bids[:levels]])
        ask_depth_basic = sum([float(ask[1]) * float(ask[0]) for ask in asks[:levels]])
        total_depth_basic = bid_depth_basic + ask_depth_basic
        
        if total_depth_basic > 0:
            basic_bir = bid_depth_basic / total_depth_basic
        else:
            basic_bir = 0.5
        
        # Calculate weighted bid depth
        bid_weight = 0.0
        for i, bid in enumerate(bids[:levels]):
            try:
                price = float(bid[0])
                volume = float(bid[1])
                
                # Calculate distance from mid price (normalized)
                distance_pct = abs(price - mid_price) / mid_price
                
                # Weight decreases with distance (closer = higher weight)
                # Weight = 1 / (distance * multiplier + 1)
                # Multiplier controls how fast weight decreases
                distance_multiplier = 100.0  # Adjust this to control weight decay
                weight = 1.0 / (distance_pct * distance_multiplier + 1.0)
                
                # Weighted depth = volume * price * weight
                bid_weight += volume * price * weight
            except (ValueError, IndexError, TypeError):
                continue
        
        # Calculate weighted ask depth
        ask_weight = 0.0
        for i, ask in enumerate(asks[:levels]):
            try:
                price = float(ask[0])
                volume = float(ask[1])
                
                # Calculate distance from mid price (normalized)
                distance_pct = abs(price - mid_price) / mid_price
                
                # Weight decreases with distance
                distance_multiplier = 100.0
                weight = 1.0 / (distance_pct * distance_multiplier + 1.0)
                
                # Weighted depth = volume * price * weight
                ask_weight += volume * price * weight
            except (ValueError, IndexError, TypeError):
                continue
        
        # Calculate WOBI
        total_weight = bid_weight + ask_weight
        if total_weight > 0:
            wobi = bid_weight / total_weight
        else:
            wobi = 0.5
        
        # Calculate imbalance strength (how far from 0.5)
        imbalance_strength = abs(wobi - 0.5) * 2.0  # 0-1 range
        
        # Determine signal
        if wobi > 0.6:  # Strong buy pressure
            signal = 'BULLISH'
        elif wobi < 0.4:  # Strong sell pressure
            signal = 'BEARISH'
        else:
            signal = 'NEUTRAL'
        
        # Calculate WOBI advantage (difference from basic BIR)
        wobi_advantage = abs(wobi - basic_bir)
        
        return {
            'wobi': wobi,
            'bid_weight': bid_weight,
            'ask_weight': ask_weight,
            'imbalance_strength': imbalance_strength,
            'signal': signal,
            'basic_bir': basic_bir,
            'wobi_advantage': wobi_advantage,
            'mid_price': mid_price,
            'best_bid': best_bid,
            'best_ask': best_ask
        }
    
    except Exception as e:
        return {
            'wobi': 0.5,
            'bid_weight': 0.0,
            'ask_weight': 0.0,
            'imbalance_strength': 0.0,
            'signal': 'NEUTRAL',
            'basic_bir': 0.5,
            'wobi_advantage': 0.0,
            'error': str(e)
        }


def get_wobi_signal_strength(wobi_data: Dict) -> float:
    """
    Get signal strength from WOBI data (0-1, where 1 = strongest signal)
    
    Args:
        wobi_data: Result from calculate_weighted_orderbook_imbalance()
    
    Returns:
        Signal strength (0-1)
    """
    if 'error' in wobi_data:
        return 0.0
    
    wobi = wobi_data.get('wobi', 0.5)
    imbalance_strength = wobi_data.get('imbalance_strength', 0.0)
    wobi_advantage = wobi_data.get('wobi_advantage', 0.0)
    
    # Signal strength combines imbalance strength and WOBI advantage
    # WOBI advantage shows hidden orders (difference from basic BIR)
    base_strength = imbalance_strength
    
    # Boost if WOBI advantage is high (indicates hidden orders)
    if wobi_advantage > 0.1:
        base_strength = min(1.0, base_strength * (1.0 + wobi_advantage))
    
    return min(1.0, base_strength)


def should_enter_based_on_wobi(
    wobi_data: Dict,
    min_wobi: float = 0.55,
    min_strength: float = 0.3
) -> Tuple[bool, str]:
    """
    Determine if should enter based on WOBI data
    
    Args:
        wobi_data: Result from calculate_weighted_orderbook_imbalance()
        min_wobi: Minimum WOBI value for bullish signal (default 0.55)
        min_strength: Minimum signal strength required (0-1)
    
    Returns:
        Tuple of (should_enter, reason)
    """
    if 'error' in wobi_data:
        return False, f"Error: {wobi_data['error']}"
    
    wobi = wobi_data.get('wobi', 0.5)
    signal = wobi_data.get('signal', 'NEUTRAL')
    strength = get_wobi_signal_strength(wobi_data)
    
    if signal == 'BEARISH':
        return False, f"Bearish WOBI signal (WOBI: {wobi:.3f}) - avoid entry"
    
    if wobi < min_wobi:
        return False, f"WOBI too low: {wobi:.3f} < {min_wobi:.3f}"
    
    if strength < min_strength:
        return False, f"WOBI strength too low: {strength:.2f} < {min_strength:.2f}"
    
    if signal == 'BULLISH':
        return True, f"Bullish WOBI signal (WOBI: {wobi:.3f}, strength: {strength:.2f})"
    else:
        return False, f"Neutral WOBI signal (WOBI: {wobi:.3f})"


def detect_hidden_orders_with_wobi(
    orderbook: Dict,
    current_price: Optional[float] = None,
    threshold: float = 0.15
) -> Dict[str, any]:
    """
    Detect hidden orders using WOBI advantage (difference from basic BIR)
    
    Hidden orders are detected when WOBI differs significantly from basic BIR,
    indicating weighted orders near price that aren't visible in basic calculation
    
    Args:
        orderbook: Orderbook data
        current_price: Optional current price
        threshold: Minimum WOBI advantage to consider as hidden orders (default 0.15)
    
    Returns:
        Dict dengan:
            - has_hidden_orders: Boolean
            - hidden_order_type: 'BULLISH' or 'BEARISH' or None
            - wobi_advantage: WOBI advantage value
            - wobi_data: Full WOBI data
    """
    wobi_data = calculate_weighted_orderbook_imbalance(orderbook, current_price=current_price)
    
    if 'error' in wobi_data:
        return {
            'has_hidden_orders': False,
            'hidden_order_type': None,
            'wobi_advantage': 0.0,
            'wobi_data': wobi_data,
            'error': wobi_data['error']
        }
    
    wobi_advantage = wobi_data.get('wobi_advantage', 0.0)
    wobi = wobi_data.get('wobi', 0.5)
    basic_bir = wobi_data.get('basic_bir', 0.5)
    
    if wobi_advantage < threshold:
        return {
            'has_hidden_orders': False,
            'hidden_order_type': None,
            'wobi_advantage': wobi_advantage,
            'wobi_data': wobi_data
        }
    
    # Determine hidden order type
    if wobi > basic_bir:
        # WOBI shows more bullish pressure than basic BIR
        hidden_order_type = 'BULLISH'
    else:
        # WOBI shows more bearish pressure than basic BIR
        hidden_order_type = 'BEARISH'
    
    return {
        'has_hidden_orders': True,
        'hidden_order_type': hidden_order_type,
        'wobi_advantage': wobi_advantage,
        'wobi_data': wobi_data,
        'interpretation': f"Hidden {hidden_order_type.lower()} orders detected near price (advantage: {wobi_advantage:.3f})"
    }


__all__ = [
    'calculate_weighted_orderbook_imbalance',
    'get_wobi_signal_strength',
    'should_enter_based_on_wobi',
    'detect_hidden_orders_with_wobi'
]

