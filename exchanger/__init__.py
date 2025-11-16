"""
Exchange module - Factory for creating exchange clients and WebSocket managers
"""
from typing import TYPE_CHECKING
from .base import BaseExchangeClient, BaseWebSocketManager
from .factory import ExchangeFactory

# Import all exchange implementations for easy access
if TYPE_CHECKING:
    # Type checking only - suppress warnings
    from .bybit import BybitClient, BybitWebSocketManager  # type: ignore
    from .binance import BinanceClient, BinanceWebSocketManager  # type: ignore
    from .okx import OKXClient, OKXWebSocketManager  # type: ignore
    from .bitget import BitgetClient, BitgetWebSocketManager  # type: ignore
    from .kucoin import KucoinClient, KucoinWebSocketManager  # type: ignore
    from .mexc import MEXCClient, MEXCWebSocketManager  # type: ignore
    from .gateio import GateIOClient, GateIOWebSocketManager  # type: ignore
else:
    # Runtime imports
    try:
        from .bybit import BybitClient, BybitWebSocketManager  # type: ignore[reportMissingImports]
    except ImportError:
        BybitClient = None  # type: ignore
        BybitWebSocketManager = None  # type: ignore

    try:
        from .binance import BinanceClient, BinanceWebSocketManager  # type: ignore[reportMissingImports]
    except ImportError:
        BinanceClient = None  # type: ignore
        BinanceWebSocketManager = None  # type: ignore

    try:
        from .okx import OKXClient, OKXWebSocketManager  # type: ignore[reportMissingImports]
    except ImportError:
        OKXClient = None  # type: ignore
        OKXWebSocketManager = None  # type: ignore

    try:
        from .bitget import BitgetClient, BitgetWebSocketManager  # type: ignore[reportMissingImports]
    except ImportError:
        BitgetClient = None  # type: ignore
        BitgetWebSocketManager = None  # type: ignore

    try:
        from .kucoin import KucoinClient, KucoinWebSocketManager  # type: ignore[reportMissingImports]
    except ImportError:
        KucoinClient = None  # type: ignore
        KucoinWebSocketManager = None  # type: ignore

    try:
        from .mexc import MEXCClient, MEXCWebSocketManager  # type: ignore[reportMissingImports]
    except ImportError:
        MEXCClient = None  # type: ignore
        MEXCWebSocketManager = None  # type: ignore

    try:
        from .gateio import GateIOClient, GateIOWebSocketManager  # type: ignore[reportMissingImports]
    except ImportError:
        GateIOClient = None  # type: ignore
        GateIOWebSocketManager = None  # type: ignore

__all__ = [
    'BaseExchangeClient', 'BaseWebSocketManager', 'ExchangeFactory',
    'BybitClient', 'BybitWebSocketManager',
    'BinanceClient', 'BinanceWebSocketManager',
    'OKXClient', 'OKXWebSocketManager',
    'BitgetClient', 'BitgetWebSocketManager',
    'KucoinClient', 'KucoinWebSocketManager',
    'MEXCClient', 'MEXCWebSocketManager',
    'GateIOClient', 'GateIOWebSocketManager'
]

