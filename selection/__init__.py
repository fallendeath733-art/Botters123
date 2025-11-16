"""
Coin selection modules for trading bot.
Contains watchlist management, cooldown, rotation, and coin classification.
"""

from .watchlist import load_coin_watchlist
from .cooldown import mark_coin_cooldown, is_coin_in_cooldown, get_cooldown_remaining
from .rotation import mark_coin_skip_rotation, is_coin_skipped_for_rotation, get_skip_rotations_remaining

# Classification module will be added later (complex dependencies)
# from .classification import get_coin_type_range

__all__ = [
    'load_coin_watchlist',
    'mark_coin_cooldown', 'is_coin_in_cooldown', 'get_cooldown_remaining',
    'mark_coin_skip_rotation', 'is_coin_skipped_for_rotation', 'get_skip_rotations_remaining',
]

