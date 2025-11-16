"""
Analysis modules for trading bot.
Contains advanced market analysis functions.
"""

from .divergence_analysis import (
    analyze_divergence,
    get_divergence_signal_strength,
    should_enter_based_on_divergence,
    DivergenceType
)

from .wobi_analysis import (
    calculate_weighted_orderbook_imbalance,
    get_wobi_signal_strength,
    should_enter_based_on_wobi,
    detect_hidden_orders_with_wobi
)

from .vwap_analysis import (
    calculate_vwap,
    calculate_multi_timeframe_vwap,
    calculate_multi_timeframe_vwap_async,
    analyze_vwap_trend,
    get_vwap_entry_signal,
    get_vwap_exit_signal
)

from .open_interest_analysis import (
    get_open_interest_data,
    get_long_short_ratio_data,
    analyze_open_interest_sentiment,
    get_oi_ls_entry_signal,
    get_oi_ls_exit_signal,
    calculate_leverage_risk
)

from .price_impact_analysis import (
    calculate_price_impact,
    get_optimal_order_size,
    should_execute_based_on_impact
)

from .multi_timeframe import (
    TrendDirection,
    calculate_trend_from_candles,
    calculate_momentum_from_candles,
    check_multi_timeframe_alignment,
    get_multi_timeframe_signal_strength
)

from .bollinger_adx import (
    calculate_bollinger_bands,
    calculate_adx,
    analyze_bollinger_adx,
    get_bollinger_adx_entry_signal,
    get_bollinger_adx_exit_signal
)

from .trade_size_analysis import (
    calculate_trade_size_distribution,
    get_dynamic_institutional_threshold,
    detect_institutional_activity,
    analyze_trade_size_trend,
    get_trade_size_entry_signal
)

from .funding_rate_analysis import (
    analyze_funding_rate_signal
)

from .adaptive_thresholds import (
    get_adaptive_divergence_threshold,
    get_adaptive_confidence_drop_threshold,
    get_adaptive_wobi_threshold,
    get_adaptive_vwap_threshold,
    get_adaptive_volume_manipulation_threshold
)

__all__ = [
    # Divergence
    'analyze_divergence',
    'get_divergence_signal_strength',
    'should_enter_based_on_divergence',
    'DivergenceType',
    # WOBI
    'calculate_weighted_orderbook_imbalance',
    'get_wobi_signal_strength',
    'should_enter_based_on_wobi',
    'detect_hidden_orders_with_wobi',
    # VWAP
    'calculate_vwap',
    'calculate_multi_timeframe_vwap',
    'calculate_multi_timeframe_vwap_async',
    'analyze_vwap_trend',
    'get_vwap_entry_signal',
    'get_vwap_exit_signal',
    # Open Interest
    'get_open_interest_data',
    'get_long_short_ratio_data',
    'analyze_open_interest_sentiment',
    'get_oi_ls_entry_signal',
    'get_oi_ls_exit_signal',
    'calculate_leverage_risk',
    # Price Impact
    'calculate_price_impact',
    'get_optimal_order_size',
    'should_execute_based_on_impact',
    # Multi-Timeframe
    'TrendDirection',
    'calculate_trend_from_candles',
    'calculate_momentum_from_candles',
    'check_multi_timeframe_alignment',
    'get_multi_timeframe_signal_strength',
    # Bollinger + ADX
    'calculate_bollinger_bands',
    'calculate_adx',
    'analyze_bollinger_adx',
    'get_bollinger_adx_entry_signal',
    'get_bollinger_adx_exit_signal',
    # Trade Size
    'calculate_trade_size_distribution',
    'get_dynamic_institutional_threshold',
    'detect_institutional_activity',
    'analyze_trade_size_trend',
    'get_trade_size_entry_signal',
    # Funding Rate
    'analyze_funding_rate_signal',
    # Adaptive Thresholds
    'get_adaptive_divergence_threshold',
    'get_adaptive_confidence_drop_threshold',
    'get_adaptive_wobi_threshold',
    'get_adaptive_vwap_threshold',
    'get_adaptive_volume_manipulation_threshold',
]

