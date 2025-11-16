"""
Rotation mode management for coin selection.
"""
import asyncio
from typing import Dict, Tuple
from collections import defaultdict

# Global rotation tracking: {symbol: (current_rotation, skip_until_rotation)}
_coin_rotation_skips: Dict[str, Tuple[int, int]] = {}
_rotation_lock = None


def _get_rotation_lock():
    """Get or create rotation lock."""
    global _rotation_lock
    if _rotation_lock is None:
        _rotation_lock = asyncio.Lock()
    return _rotation_lock


async def mark_coin_skip_rotation(symbol: str, current_rotation: int, skip_rotations: int = 2):
    """Mark a coin to skip for specified number of rotations."""
    async with _get_rotation_lock():
        skip_until = current_rotation + skip_rotations
        _coin_rotation_skips[symbol] = (current_rotation, skip_until)


async def is_coin_skipped_for_rotation(symbol: str, current_rotation: int) -> bool:
    """Check if a coin is skipped for current rotation."""
    async with _get_rotation_lock():
        if symbol not in _coin_rotation_skips:
            return False
        _, skip_until = _coin_rotation_skips[symbol]
        return current_rotation < skip_until


async def get_skip_rotations_remaining(symbol: str, current_rotation: int) -> int:
    """Get remaining skip rotations."""
    async with _get_rotation_lock():
        if symbol not in _coin_rotation_skips:
            return 0
        _, skip_until = _coin_rotation_skips[symbol]
        remaining = skip_until - current_rotation
        return max(0, remaining)


__all__ = [
    'mark_coin_skip_rotation',
    'is_coin_skipped_for_rotation',
    'get_skip_rotations_remaining',
]

