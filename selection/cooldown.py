"""
Cooldown mechanism for coin selection.
"""
import asyncio
from typing import Dict
from collections import defaultdict

# Global cooldown tracking
_coin_cooldowns: Dict[str, float] = {}
_cooldown_lock = None


def _get_cooldown_lock():
    """Get or create cooldown lock."""
    global _cooldown_lock
    if _cooldown_lock is None:
        _cooldown_lock = asyncio.Lock()
    return _cooldown_lock


async def mark_coin_cooldown(symbol: str, cooldown_seconds: int = 300):
    """Mark a coin as in cooldown."""
    async with _get_cooldown_lock():
        import time
        _coin_cooldowns[symbol] = time.time() + cooldown_seconds


async def is_coin_in_cooldown(symbol: str) -> bool:
    """Check if a coin is in cooldown."""
    async with _get_cooldown_lock():
        import time
        if symbol not in _coin_cooldowns:
            return False
        return time.time() < _coin_cooldowns[symbol]


async def get_cooldown_remaining(symbol: str) -> int:
    """Get remaining cooldown time in seconds."""
    async with _get_cooldown_lock():
        import time
        if symbol not in _coin_cooldowns:
            return 0
        remaining = _coin_cooldowns[symbol] - time.time()
        return max(0, int(remaining))


__all__ = [
    'mark_coin_cooldown',
    'is_coin_in_cooldown',
    'get_cooldown_remaining',
]

