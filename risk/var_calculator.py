"""
Value at Risk (VaR) calculator for risk assessment.
"""
import time
import math
import numpy as np
from typing import List, Dict
from utils.helpers import safe_division
from config.colors import YELLOW, RED, RESET


class VaRCalculator:
    """Calculate Value at Risk (VaR) and Conditional VaR (CVaR)."""
    
    def __init__(self):
        self.historical_data = {}
        self.confidence_level = 0.95
        self.max_data_points_per_symbol = 5000

    def calculate_var(self, returns, confidence_level=0.95):
        """Calculate Value at Risk from returns."""
        if not returns or len(returns) == 0:
            return 0.0
        try:
            returns_array = np.array(returns)
            var = np.percentile(returns_array, (1 - confidence_level) * 100)
            return abs(var)
        except Exception as e:
            print(f"{YELLOW}⚠️ VaR calculation error: {e}{RESET}")
            if not hasattr(self, 'var_fallback_counter'):
                self.var_fallback_counter = 0
            self.var_fallback_counter += 1
            if self.var_fallback_counter > 5:
                print(f"{RED}Too many VaR calculation errors, exiting.{RESET}")
                import sys
                sys.exit(1)
            import asyncio
            asyncio.create_task(asyncio.sleep(5))
            return 0.0

    def calculate_conditional_var(self, returns, confidence_level=0.95):
        """Calculate Conditional Value at Risk (CVaR)."""
        if not returns or len(returns) == 0:
            return 0.0
        try:
            returns_array = np.array(returns)
            var = self.calculate_var(returns, confidence_level)
            tail_losses = returns_array[returns_array <= -var]
            if len(tail_losses) > 0:
                cvar = np.mean(tail_losses)
                return abs(cvar)
            else:
                return var * 1.5
        except Exception as e:
            print(f"{YELLOW}⚠️ CVaR calculation error: {e}{RESET}")
            return self.calculate_var(returns, confidence_level) * 1.3

    def update_historical_data(self, symbol: str, price_data: List[float]):
        """Update historical price data for a symbol."""
        if symbol not in self.historical_data:
            self.historical_data[symbol] = []
        self.historical_data[symbol].extend(price_data)
        if len(self.historical_data[symbol]) > 1000:
            self.historical_data[symbol] = self.historical_data[symbol][-1000:]

    def calculate_returns(self, prices: List[float]) -> List[float]:
        """Calculate log returns from price data."""
        if len(prices) < 2:
            return []
        returns = []
        for i in range(1, len(prices)):
            if prices[i-1] > 0 and prices[i] > 0:
                ret = math.log(safe_division(prices[i], prices[i-1], 1.0))
                returns.append(ret)
        return returns

    def cleanup_old_data(self, max_age_days: int = 7):
        """Cleanup old historical data."""
        try:
            current_time = time.time()
            cutoff_time = current_time - (max_age_days * 86400)
            symbols_to_remove = []
            for symbol, data_list in self.historical_data.items():
                if len(data_list) > self.max_data_points_per_symbol:
                    self.historical_data[symbol] = data_list[-self.max_data_points_per_symbol:]
                if not self.historical_data[symbol]:
                    symbols_to_remove.append(symbol)
            for symbol in symbols_to_remove:
                del self.historical_data[symbol]
        except Exception as e:
            print(f"{YELLOW}⚠️ VaR cleanup error: {e}{RESET}")

    def clear_all_data(self):
        """Clear all historical data."""
        self.historical_data.clear()


__all__ = ['VaRCalculator']

