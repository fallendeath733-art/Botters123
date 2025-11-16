"""
Adaptive Thresholds Module
Adaptive thresholds untuk berbagai analisa berdasarkan coin type dan volatility
"""

from typing import Dict


def get_adaptive_divergence_threshold(coin_type: str, volatility: float) -> Dict[str, float]:
    """
    Get adaptive divergence threshold berdasarkan coin type dan volatility
    
    Args:
        coin_type: Coin type (BLUECHIP, MAJOR, ALTCOIN, MICROCAP)
        volatility: Price volatility (0-1)
    
    Returns:
        Dict dengan thresholds
    """
    # Base thresholds by coin type
    base_thresholds = {
        'BLUECHIP': 0.3,
        'MAJOR': 0.25,
        'ALTCOIN': 0.2,
        'MICROCAP': 0.15
    }
    
    base = base_thresholds.get(coin_type, 0.25)
    
    # Adjust based on volatility (higher volatility = lower threshold needed)
    volatility_adjustment = 1.0 - (volatility * 0.3)  # Reduce threshold by up to 30%
    threshold = base * volatility_adjustment
    
    return {
        'min_strength': max(0.1, threshold),
        'base_threshold': base,
        'volatility_adjustment': volatility_adjustment
    }


def get_adaptive_confidence_drop_threshold(
    coin_type: str,
    initial_confidence: float,
    volatility: float
) -> Dict[str, float]:
    """
    Get adaptive confidence drop threshold
    
    Args:
        coin_type: Coin type
        initial_confidence: Initial confidence (0-100)
        volatility: Price volatility
    
    Returns:
        Dict dengan thresholds
    """
    # Base drop thresholds
    base_drops = {
        'BLUECHIP': 10.0,  # 10% drop
        'MAJOR': 15.0,
        'ALTCOIN': 20.0,
        'MICROCAP': 25.0
    }
    
    base_drop = base_drops.get(coin_type, 15.0)
    
    # Adjust based on volatility
    volatility_adjustment = 1.0 + (volatility * 0.5)  # Increase drop tolerance with volatility
    max_drop = base_drop * volatility_adjustment
    
    return {
        'max_drop_pct': min(max_drop, 30.0),  # Cap at 30%
        'base_drop': base_drop,
        'volatility_adjustment': volatility_adjustment
    }


def get_adaptive_wobi_threshold(coin_type: str, volatility: float) -> Dict[str, float]:
    """
    Get adaptive WOBI threshold
    
    Args:
        coin_type: Coin type
        volatility: Price volatility
    
    Returns:
        Dict dengan thresholds
    """
    # Base WOBI thresholds
    base_wobi = {
        'BLUECHIP': 0.55,
        'MAJOR': 0.57,
        'ALTCOIN': 0.60,
        'MICROCAP': 0.65
    }
    
    base = base_wobi.get(coin_type, 0.57)
    
    # Adjust based on volatility
    volatility_adjustment = 1.0 + (volatility * 0.1)  # Higher volatility = higher threshold
    threshold = base * volatility_adjustment
    
    return {
        'min_wobi': min(threshold, 0.7),  # Cap at 0.7
        'base_wobi': base,
        'volatility_adjustment': volatility_adjustment
    }


def get_adaptive_vwap_threshold(coin_type: str, volatility: float) -> Dict[str, float]:
    """
    Get adaptive VWAP threshold
    
    Args:
        coin_type: Coin type
        volatility: Price volatility
    
    Returns:
        Dict dengan thresholds
    """
    # Base VWAP distance thresholds
    base_distances = {
        'BLUECHIP': 1.0,  # 1% from VWAP
        'MAJOR': 1.5,
        'ALTCOIN': 2.0,
        'MICROCAP': 3.0
    }
    
    base = base_distances.get(coin_type, 1.5)
    
    # Adjust based on volatility
    volatility_adjustment = 1.0 + (volatility * 0.5)
    threshold = base * volatility_adjustment
    
    return {
        'min_distance_pct': threshold,
        'base_distance': base,
        'volatility_adjustment': volatility_adjustment
    }


def get_adaptive_volume_manipulation_threshold(coin_type: str, volatility: float) -> Dict[str, float]:
    """
    Get adaptive volume manipulation threshold
    
    Args:
        coin_type: Coin type
        volatility: Price volatility
    
    Returns:
        Dict dengan thresholds
    """
    # Base volume thresholds
    base_volumes = {
        'BLUECHIP': 2.0,  # 2x average volume
        'MAJOR': 2.5,
        'ALTCOIN': 3.0,
        'MICROCAP': 4.0
    }
    
    base = base_volumes.get(coin_type, 2.5)
    
    # Adjust based on volatility
    volatility_adjustment = 1.0 + (volatility * 0.3)
    threshold = base * volatility_adjustment
    
    return {
        'min_volume_ratio': threshold,
        'base_volume': base,
        'volatility_adjustment': volatility_adjustment
    }


__all__ = [
    'get_adaptive_divergence_threshold',
    'get_adaptive_confidence_drop_threshold',
    'get_adaptive_wobi_threshold',
    'get_adaptive_vwap_threshold',
    'get_adaptive_volume_manipulation_threshold'
]

