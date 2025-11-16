"""
Anti-panic dump detection and prevention.
"""
import time
from collections import deque
from typing import Tuple, Dict
from utils.helpers import safe_division


class AntiPanicDump:
    """Detect and prevent panic dump scenarios."""
    
    def __init__(self):
        self.price_history = deque(maxlen=200)
        self.volume_history = deque(maxlen=200)
        self.panic_cooldown = 300
        self.last_panic_time = 0
        self.max_price_history = 200

    def add_price_data(self, price: float, volume: float = 0):
        """Add price and volume data to history."""
        now = time.time()
        self.price_history.append((now, price))
        if volume > 0:
            self.volume_history.append((now, volume))
        cutoff_time = now - 300
        while self.price_history and self.price_history[0][0] < cutoff_time:
            self.price_history.popleft()
        while self.volume_history and self.volume_history[0][0] < cutoff_time:
            self.volume_history.popleft()

    def detect_panic_dump(self) -> Tuple[bool, str, dict]:
        """Detect panic dump patterns."""
        if len(self.price_history) < 5:
            return False, "", {}
        if time.time() - self.last_panic_time < self.panic_cooldown:
            return False, "In cooldown", {}
        now = time.time()
        prices = [p for t, p in self.price_history]
        time_window_start = now - 300
        recent_prices = [(t, p) for t, p in self.price_history if t > time_window_start]
        if len(recent_prices) >= 3:
            first_price = recent_prices[0][1]
            current_price = recent_prices[-1][1]
            drop_pct = safe_division((current_price - first_price), first_price, 0.0) * 100
            if drop_pct < -5.0:
                if len(self.volume_history) >= 3:
                    volumes = [v for t, v in self.volume_history]
                    avg_volume = sum(volumes[:-1]) / max(1, len(volumes) - 1)
                    current_volume = volumes[-1]
                    volume_spike = current_volume / avg_volume if avg_volume > 0 else 1.0
                    if volume_spike > 3.0:
                        self.last_panic_time = now
                        return True, "Rapid drop with volume spike", {
                            "drop_pct": drop_pct,
                            "volume_spike": volume_spike
                        }
        if len(prices) >= 5:
            last_5_prices = prices[-5:]
            red_count = sum(1 for i in range(1, len(last_5_prices)) if last_5_prices[i] < last_5_prices[i-1])
            if red_count >= 4:
                total_drop = safe_division((last_5_prices[-1] - last_5_prices[0]), last_5_prices[0], 0.0) * 100
                if total_drop < -3.0:
                    self.last_panic_time = now
                    return True, "Consecutive red candles", {"red_count": red_count, "total_drop": total_drop}
        if len(prices) >= 2:
            instant_drop = safe_division((prices[-1] - prices[-2]), prices[-2], 0.0) * 100
            if instant_drop < -2.0:
                self.last_panic_time = now
                return True, "Flash crash detected", {"instant_drop": instant_drop}
        return False, "", {}

    def is_in_cooldown(self) -> bool:
        """Check if in cooldown period."""
        return (time.time() - self.last_panic_time) < self.panic_cooldown

    def get_cooldown_remaining(self) -> int:
        """Get remaining cooldown time in seconds."""
        if not self.is_in_cooldown():
            return 0
        return int(self.panic_cooldown - (time.time() - self.last_panic_time))

    def reset(self):
        """Reset panic dump detector."""
        self.price_history.clear()
        self.volume_history.clear()
        self.last_panic_time = 0


# Global anti-panic dump instance
anti_panic_dump = AntiPanicDump()

__all__ = ['AntiPanicDump', 'anti_panic_dump']

