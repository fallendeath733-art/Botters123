"""
Watchlist management for coin selection.
"""
import os
from typing import List
from config.constants import CONFIG_FILE


def load_coin_watchlist(file_path: str = None) -> List[str]:
    """
    Load coin watchlist from file.
    
    Args:
        file_path: Path to coinlist file. If None, uses default 'coinlist.txt'
        
    Returns:
        List of coin symbols
    """
    if file_path is None:
        # Default to coinlist.txt in same directory as config
        config_dir = os.path.dirname(os.path.abspath(CONFIG_FILE)) if CONFIG_FILE else os.getcwd()
        file_path = os.path.join(config_dir, "coinlist.txt")
    
    if not os.path.exists(file_path):
        return []
    
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
        coins = []
        for line in lines:
            line = line.strip()
            if line and not line.startswith('#'):
                coins.append(line.upper())
        return coins
    except Exception:
        return []


__all__ = ['load_coin_watchlist']

