"""
Factory for creating exchange clients and WebSocket managers
"""
from typing import Optional, List, TYPE_CHECKING
from .base import BaseExchangeClient, BaseWebSocketManager

# Import all exchange implementations
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


class ExchangeFactory:
    """Factory to create exchange clients based on configuration"""
    _supported_exchanges = {}
    _initialized = False
    
    @staticmethod
    def _initialize():
        """Initialize and register all supported exchanges"""
        if ExchangeFactory._initialized:
            return
        
        # Register all exchanges
        if BybitClient:
            ExchangeFactory.register_exchange('BYBIT', BybitClient, BybitWebSocketManager)
        
        if BinanceClient:
            # NOTE: Binance doesn't have WebSocket manager, use None
            ExchangeFactory.register_exchange('BINANCE', BinanceClient, BinanceWebSocketManager)
        
        if OKXClient:
            ExchangeFactory.register_exchange('OKX', OKXClient, OKXWebSocketManager)
        
        if BitgetClient:
            ExchangeFactory.register_exchange('BITGET', BitgetClient, BitgetWebSocketManager)
        
        if KucoinClient:
            ExchangeFactory.register_exchange('KUCOIN', KucoinClient, KucoinWebSocketManager)
        
        if MEXCClient:
            ExchangeFactory.register_exchange('MEXC', MEXCClient, MEXCWebSocketManager)
        
        if GateIOClient:
            ExchangeFactory.register_exchange('GATEIO', GateIOClient, GateIOWebSocketManager)
        
        ExchangeFactory._initialized = True
    
    @staticmethod
    def register_exchange(name: str, client_class, ws_class=None):
        """Register an exchange with its client and WebSocket classes"""
        ExchangeFactory._supported_exchanges[name.upper()] = {
            'client': client_class,
            'ws': ws_class
        }
    
    @staticmethod
    def create_client(exchange_name: str) -> Optional[BaseExchangeClient]:
        """Create exchange client instance"""
        ExchangeFactory._initialize()
        exchange_name = exchange_name.upper()
        if exchange_name not in ExchangeFactory._supported_exchanges:
            try:
                from core import RED, CYAN, RESET
                print(f"{RED}❌ Unsupported exchange: {exchange_name}{RESET}")
                print(f"{CYAN}Supported exchanges: {', '.join(ExchangeFactory._supported_exchanges.keys())}{RESET}")
            except:
                print(f"❌ Unsupported exchange: {exchange_name}")
            return None
        client_class = ExchangeFactory._supported_exchanges[exchange_name]['client']
        return client_class()
    
    @staticmethod
    def create_ws_manager(exchange_name: str) -> Optional[BaseWebSocketManager]:
        """Create WebSocket manager instance"""
        ExchangeFactory._initialize()
        exchange_name = exchange_name.upper()
        if exchange_name not in ExchangeFactory._supported_exchanges:
            return None
        ws_class = ExchangeFactory._supported_exchanges[exchange_name].get('ws')
        if ws_class is None:
            return None
        return ws_class()
    
    @staticmethod
    def get_supported_exchanges() -> List[str]:
        """Get list of supported exchange names"""
        ExchangeFactory._initialize()
        return list(ExchangeFactory._supported_exchanges.keys())

