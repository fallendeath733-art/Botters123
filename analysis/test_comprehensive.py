"""
Comprehensive Test & Simulation untuk semua Analysis Modules
Menguji semua modul dengan berbagai skenario
"""

import asyncio
import time
from typing import Dict, List


async def simulate_market_scenario(scenario_name: str, test_data: Dict):
    """Simulate market scenario dengan berbagai analysis modules"""
    print(f"\n{'='*80}")
    print(f"SCENARIO: {scenario_name}")
    print(f"{'='*80}")
    
    results = {}
    
    try:
        # Import all modules (using relative imports)
        from .divergence_analysis import analyze_divergence, DivergenceType
        from .wobi_analysis import calculate_weighted_orderbook_imbalance, should_enter_based_on_wobi
        from .vwap_analysis import calculate_vwap, analyze_vwap_trend, get_vwap_entry_signal
        from .open_interest_analysis import analyze_open_interest_sentiment, get_oi_ls_entry_signal
        from .price_impact_analysis import calculate_price_impact, should_execute_based_on_impact
        from .multi_timeframe import calculate_trend_from_candles, TrendDirection
        from .bollinger_adx import analyze_bollinger_adx, get_bollinger_adx_entry_signal
        from .trade_size_analysis import detect_institutional_activity, get_trade_size_entry_signal
        from .funding_rate_analysis import analyze_funding_rate_signal
        from .adaptive_thresholds import get_adaptive_divergence_threshold
        
        # Test 1: Divergence Analysis
        if 'candles' in test_data:
            candles = test_data['candles']
            divergence_result = await analyze_divergence('BTC_USDT', candles)
            results['divergence'] = divergence_result
            print(f"✅ Divergence: {divergence_result.get('overall_signal', 'N/A')}")
        
        # Test 2: WOBI Analysis
        if 'orderbook' in test_data:
            orderbook = test_data['orderbook']
            wobi_result = calculate_weighted_orderbook_imbalance(orderbook, current_price=test_data.get('price', 100))
            should_enter, reason = should_enter_based_on_wobi(wobi_result)
            results['wobi'] = {'should_enter': should_enter, 'reason': reason}
            print(f"✅ WOBI: {wobi_result.get('signal', 'N/A')} (WOBI: {wobi_result.get('wobi', 0):.3f})")
        
        # Test 3: VWAP Analysis
        if 'candles' in test_data:
            vwap = calculate_vwap(candles)
            if vwap:
                vwap_analysis = analyze_vwap_trend(test_data.get('price', 100), vwap)
                should_enter, reason = get_vwap_entry_signal(vwap_analysis)
                results['vwap'] = {'should_enter': should_enter, 'reason': reason}
                print(f"✅ VWAP: {vwap_analysis.get('trend', 'N/A')} (VWAP: {vwap:.2f})")
        
        # Test 4: Open Interest Analysis
        if 'oi_data' in test_data or 'ls_data' in test_data:
            oi_result = await analyze_open_interest_sentiment(
                'BTC_USDT',
                oi_data=test_data.get('oi_data'),
                ls_data=test_data.get('ls_data')
            )
            should_enter, reason = get_oi_ls_entry_signal(oi_result)
            results['oi'] = {'should_enter': should_enter, 'reason': reason}
            print(f"✅ OI/LS: {oi_result.get('sentiment', 'N/A')}")
        
        # Test 5: Price Impact Analysis
        if 'orderbook' in test_data:
            order_size = test_data.get('order_size', 1000)
            impact_result = calculate_price_impact(order_size, test_data['orderbook'], test_data.get('price', 100))
            should_execute, reason = should_execute_based_on_impact(impact_result)
            results['price_impact'] = {'should_execute': should_execute, 'reason': reason}
            print(f"✅ Price Impact: {impact_result.get('impact_level', 'N/A')} ({impact_result.get('price_impact_pct', 0):.2f}%)")
        
        # Test 6: Multi-Timeframe
        if 'candles' in test_data:
            trend = calculate_trend_from_candles(test_data['candles'])
            results['multi_tf'] = {'trend': trend.value}
            print(f"✅ Multi-TF: {trend.value}")
        
        # Test 7: Bollinger + ADX
        if 'candles' in test_data:
            bb_adx_result = await analyze_bollinger_adx(test_data['candles'], current_price=test_data.get('price', 100))
            should_enter, reason = get_bollinger_adx_entry_signal(bb_adx_result)
            results['bb_adx'] = {'should_enter': should_enter, 'reason': reason}
            print(f"✅ BB+ADX: {bb_adx_result.get('signal', 'N/A')} (ADX: {bb_adx_result.get('adx', 0):.1f})")
        
        # Test 8: Trade Size Analysis
        if 'trades' in test_data:
            activity = detect_institutional_activity(test_data['trades'])
            trend = {'trend': 'STABLE', 'trend_strength': 0.0}  # Simplified
            should_enter, reason = get_trade_size_entry_signal(activity, trend)
            results['trade_size'] = {'should_enter': should_enter, 'reason': reason}
            print(f"✅ Trade Size: {activity.get('activity_level', 'N/A')}")
        
        # Test 9: Funding Rate
        if 'funding_rate' in test_data:
            funding_result = analyze_funding_rate_signal(test_data['funding_rate'])
            results['funding'] = funding_result
            print(f"✅ Funding Rate: {funding_result.get('sentiment', 'N/A')} ({test_data['funding_rate']:.4f}%)")
        
        # Test 10: Adaptive Thresholds
        coin_type = test_data.get('coin_type', 'MAJOR')
        volatility = test_data.get('volatility', 0.02)
        div_thresh = get_adaptive_divergence_threshold(coin_type, volatility)
        results['adaptive_thresholds'] = div_thresh
        print(f"✅ Adaptive Thresholds: {coin_type} (volatility: {volatility*100:.1f}%)")
        
        return results
    
    except Exception as e:
        print(f"❌ Error in scenario {scenario_name}: {e}")
        import traceback
        traceback.print_exc()
        return {'error': str(e)}


async def run_all_scenarios():
    """Run semua skenario simulasi"""
    print("=" * 80)
    print("COMPREHENSIVE SIMULATION - ALL ANALYSIS MODULES")
    print("=" * 80)
    
    scenarios = []
    
    # Scenario 1: Bullish Market
    scenarios.append((
        "BULLISH MARKET - Strong Uptrend",
        {
            'candles': [
                {'close': 100 + i*2, 'high': 102 + i*2, 'low': 99 + i*2, 'volume': 1000 + i*100}
                for i in range(30)
            ],
            'orderbook': {
                'bids': [[100.0, 20.0], [99.5, 25.0], [99.0, 30.0]],
                'asks': [[100.5, 15.0], [101.0, 20.0], [101.5, 25.0]]
            },
            'price': 105.0,
            'order_size': 1000,
            'oi_data': {'open_interest': 500_000_000, 'oi_change_24h': 10.0, 'timestamp': time.time()},
            'ls_data': {'long_short_ratio': 1.3, 'long_accounts': 1000, 'short_accounts': 769, 'timestamp': time.time()},
            'trades': [
                {'size': 10.0, 'price': 100.0},
                {'size': 15.0, 'price': 100.0},
                {'size': 20.0, 'price': 100.0},
            ] * 20,
            'funding_rate': -0.12,  # Negative = bullish
            'coin_type': 'MAJOR',
            'volatility': 0.02
        }
    ))
    
    # Scenario 2: Bearish Market
    scenarios.append((
        "BEARISH MARKET - Strong Downtrend",
        {
            'candles': [
                {'close': 100 - i*2, 'high': 101 - i*2, 'low': 98 - i*2, 'volume': 1000 + i*100}
                for i in range(30)
            ],
            'orderbook': {
                'bids': [[98.0, 10.0], [97.5, 15.0], [97.0, 20.0]],
                'asks': [[98.5, 25.0], [99.0, 30.0], [99.5, 35.0]]
            },
            'price': 95.0,
            'order_size': 1000,
            'oi_data': {'open_interest': 500_000_000, 'oi_change_24h': -10.0, 'timestamp': time.time()},
            'ls_data': {'long_short_ratio': 0.7, 'long_accounts': 700, 'short_accounts': 1000, 'timestamp': time.time()},
            'trades': [
                {'size': 5.0, 'price': 100.0},
                {'size': 3.0, 'price': 100.0},
            ] * 20,
            'funding_rate': 0.15,  # Positive = bearish
            'coin_type': 'MAJOR',
            'volatility': 0.03
        }
    ))
    
    # Scenario 3: Sideways Market
    scenarios.append((
        "SIDEWAYS MARKET - Consolidation",
        {
            'candles': [
                {'close': 100 + (i%3)*0.5, 'high': 101 + (i%3)*0.5, 'low': 99 + (i%3)*0.5, 'volume': 1000}
                for i in range(30)
            ],
            'orderbook': {
                'bids': [[100.0, 15.0], [99.5, 20.0], [99.0, 25.0]],
                'asks': [[100.5, 15.0], [101.0, 20.0], [101.5, 25.0]]
            },
            'price': 100.0,
            'order_size': 1000,
            'oi_data': {'open_interest': 500_000_000, 'oi_change_24h': 1.0, 'timestamp': time.time()},
            'ls_data': {'long_short_ratio': 1.0, 'long_accounts': 1000, 'short_accounts': 1000, 'timestamp': time.time()},
            'trades': [
                {'size': 10.0, 'price': 100.0},
            ] * 30,
            'funding_rate': 0.02,  # Neutral
            'coin_type': 'MAJOR',
            'volatility': 0.01
        }
    ))
    
    # Scenario 4: High Volatility
    scenarios.append((
        "HIGH VOLATILITY - Volatile Market",
        {
            'candles': [
                {'close': 100 + (i%2)*5 - 2.5, 'high': 105 + (i%2)*5, 'low': 95 + (i%2)*5, 'volume': 2000 + i*200}
                for i in range(30)
            ],
            'orderbook': {
                'bids': [[100.0, 10.0], [95.0, 15.0], [90.0, 20.0]],
                'asks': [[105.0, 10.0], [110.0, 15.0], [115.0, 20.0]]
            },
            'price': 102.0,
            'order_size': 5000,
            'oi_data': {'open_interest': 800_000_000, 'oi_change_24h': 15.0, 'timestamp': time.time()},
            'ls_data': {'long_short_ratio': 1.5, 'long_accounts': 1500, 'short_accounts': 1000, 'timestamp': time.time()},
            'trades': [
                {'size': 20.0, 'price': 100.0},
                {'size': 25.0, 'price': 100.0},
            ] * 15,
            'funding_rate': -0.08,
            'coin_type': 'ALTCOIN',
            'volatility': 0.05
        }
    ))
    
    # Run all scenarios
    all_results = {}
    for scenario_name, test_data in scenarios:
        result = await simulate_market_scenario(scenario_name, test_data)
        all_results[scenario_name] = result
        await asyncio.sleep(0.1)  # Small delay between scenarios
    
    # Summary
    print(f"\n{'='*80}")
    print("SIMULATION SUMMARY")
    print(f"{'='*80}")
    print(f"Total scenarios: {len(scenarios)}")
    print(f"Scenarios passed: {len([r for r in all_results.values() if 'error' not in r])}")
    print(f"Scenarios failed: {len([r for r in all_results.values() if 'error' in r])}")
    print(f"{'='*80}")
    print("✅ COMPREHENSIVE SIMULATION COMPLETED!")
    print(f"{'='*80}")
    
    return all_results


if __name__ == "__main__":
    asyncio.run(run_all_scenarios())

