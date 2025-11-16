"""
Portfolio management for position tracking and balance management.
"""
import asyncio
import time
from typing import Dict, Optional
from config.trading_config import TradingConfig
from utils import output_manager, simple_logger, performance_monitor, safe_division, fmt
from config.colors import GREEN, RED, YELLOW, CYAN
from market.regime import MarketRegime


class PortfolioManager:
    """Manage portfolio positions, trades, and risk monitoring."""
    
    def __init__(self):
        self.positions = {}
        self.position_lock = None
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.start_time = time.time()
        self.last_profitable_close = None
        self.consecutive_losses = 0
        self.base_portfolio_value = 10000
        self.peak_equity = 10000
        self.max_drawdown_value = 0.0
        self.last_drawdown_alert = 0
        self.session_trades = 0
        self.last_trade_profit = None

    def _get_position_lock(self):
        if self.position_lock is None:
            self.position_lock = asyncio.Lock()
        return self.position_lock

    async def get_position(self, symbol: str):
        async with self._get_position_lock():
            return self.positions.get(symbol)

    async def set_position(self, symbol: str, data: dict):
        async with self._get_position_lock():
            self.positions[symbol] = data

    async def delete_position(self, symbol: str):
        async with self._get_position_lock():
            if symbol in self.positions:
                del self.positions[symbol]

    async def has_position(self, symbol: str) -> bool:
        async with self._get_position_lock():
            return symbol in self.positions

    async def get_all_positions(self) -> dict:
        async with self._get_position_lock():
            return self.positions.copy()

    async def can_open_position(self, symbol: str, size: float, price: float, market_regime: MarketRegime = None) -> bool:
        """Check if a new position can be opened based on risk limits."""
        # Import here to avoid circular dependency
        from core import calculate_coin_volatility
        
        portfolio_size = 10000
        position_value = size * price
        if self.daily_trades >= TradingConfig.MAX_TRADES_PER_DAY:
            output_manager.print_static(f"{YELLOW}⚠️ Daily trade limit reached: {self.daily_trades}/{TradingConfig.MAX_TRADES_PER_DAY}{RESET}")
            return False
        if position_value > portfolio_size * TradingConfig.MAX_POSITION_SIZE:
            output_manager.print_static(f"{RED}❌ Position too large: ${position_value:.2f} > ${portfolio_size * TradingConfig.MAX_POSITION_SIZE:.2f}{RESET}")
            return False
        if self.daily_pnl < -TradingConfig.MAX_DAILY_LOSS * portfolio_size:
            output_manager.print_static(f"{RED}❌ Daily loss limit reached: ${self.daily_pnl:.2f}{RESET}")
            return False
        async with self._get_position_lock():
            current_positions = len(self.positions)
        if current_positions >= TradingConfig.MAX_SIMULTANEOUS_POSITIONS:
            output_manager.print_static(f"{RED}❌ Maximum positions reached: {current_positions}/{TradingConfig.MAX_SIMULTANEOUS_POSITIONS}{RESET}")
            return False
        async with self._get_position_lock():
            total_exposure = sum(
                pos.get('size', 0) * pos.get('entry_price', 0)
                for pos in self.positions.values()
            )
        total_exposure += position_value
        exposure_pct = safe_division(total_exposure, portfolio_size, 0.0)
        if exposure_pct > TradingConfig.MAX_PORTFOLIO_EXPOSURE:
            output_manager.print_static(
                f"{RED}❌ Portfolio exposure limit: {exposure_pct*100:.1f}% > {TradingConfig.MAX_PORTFOLIO_EXPOSURE*100:.0f}% "
                f"(current: ${total_exposure-position_value:.2f}, new: ${position_value:.2f}){RESET}"
            )
            return False
        try:
            btc_eth_symbols = ['BTC_USDT', 'ETH_USDT']
            correlated_exposure = sum(
                pos.get('size', 0) * pos.get('entry_price', 0)
                for sym, pos in self.positions.items()
                if any(corr in sym for corr in btc_eth_symbols)
            )
            if any(corr in symbol for corr in btc_eth_symbols):
                correlated_exposure += position_value
            correlated_pct = safe_division(correlated_exposure, portfolio_size, 0.0)
            if correlated_pct > TradingConfig.MAX_CORRELATED_EXPOSURE:
                output_manager.print_static(
                    f"{RED}❌ Correlated exposure limit: {correlated_pct*100:.1f}% > {TradingConfig.MAX_CORRELATED_EXPOSURE*100:.0f}% "
                    f"(BTC/ETH related){RESET}"
                )
                return False
        except Exception:
            pass
        # Import risk_manager here to avoid circular dependency
        from risk import risk_manager
        volatility = await calculate_coin_volatility(symbol)
        recommended_size = risk_manager.calculate_position_size(symbol, volatility, portfolio_size, market_regime)
        if position_value > recommended_size:
            regime_info = f" (regime: {market_regime.value})" if market_regime else ""
            output_manager.print_static(f"{RED}❌ Position exceeds regime-adjusted limit: ${position_value:.2f} > ${recommended_size:.2f}{regime_info}{RESET}")
            return False
        return True

    def update_drawdown_monitoring(self):
        """Update drawdown monitoring and alert if needed."""
        try:
            current_equity = self.base_portfolio_value + self.daily_pnl
            if current_equity > self.peak_equity:
                self.peak_equity = current_equity
            current_drawdown = safe_division((self.peak_equity - current_equity), self.peak_equity, 0.0) * 100
            self.max_drawdown_value = max(self.max_drawdown_value, current_drawdown)
            if current_drawdown >= TradingConfig.DRAWDOWN_ALERT_THRESHOLD * 100:
                current_time = time.time()
                if current_time - self.last_drawdown_alert >= 300:
                    self.last_drawdown_alert = current_time
                    output_manager.print_static(
                        f"{RED}🚨 DRAWDOWN ALERT: {current_drawdown:.2f}% "
                        f"(Limit: {TradingConfig.MAX_DRAWDOWN*100:.0f}%){RESET}"
                    )
            if current_drawdown >= TradingConfig.MAX_DRAWDOWN * 100:
                output_manager.print_static(
                    f"{RED}🔴 MAX DRAWDOWN EXCEEDED: {current_drawdown:.2f}% >= {TradingConfig.MAX_DRAWDOWN*100:.0f}%{RESET}"
                )
                output_manager.print_static(
                    f"{RED}⚠️  RECOMMENDATION: Review strategy or reduce position sizes{RESET}"
                )
        except Exception:
            pass

    def record_trade(self, symbol: str, action: str, price: float, size: float, pnl: float = 0,
                     market_regime: str = None, confidence: float = None, reason: str = None):
        """Record a trade execution."""
        self.daily_trades += 1
        self.daily_pnl += pnl
        performance_monitor.record_trade()
        if action == "SELL" and pnl != 0:
            self.session_trades += 1
            self.last_trade_profit = pnl
            if pnl < 0:
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0
        self.update_drawdown_monitoring()
        trade_info = {
            'symbol': symbol,
            'action': action,
            'price': price,
            'size': size,
            'timestamp': time.time(),
            'pnl': pnl,
            'market_regime': market_regime,
            'confidence': confidence,
            'reason': reason
        }
        try:
            log_reason = reason or f"{action} executed"
            if action == "SELL" and pnl != 0:
                log_reason = f"Exit P/L: {pnl:+.2f}%"
            context_parts = []
            if market_regime:
                context_parts.append(f"Regime: {market_regime}")
            if confidence is not None:
                context_parts.append(f"Confidence: {confidence:.0f}%")
            if context_parts:
                log_reason += " | " + ", ".join(context_parts)
            simple_logger.log_trade(symbol, action, price, pnl, 0, log_reason)
        except Exception as e:
            simple_logger.log_error("Trade logging", str(e))
        print(f"{GREEN}📊 Trade recorded: {action} {size} {symbol} at ${fmt(price)}{RESET}")

    def record_trade_with_performance(self, symbol: str, action: str, price: float, size: float, pnl: float = 0):
        """Record trade with performance tracking."""
        self.record_trade(symbol, action, price, size, pnl)
        performance_monitor.record_trade()
        if pnl != 0:
            pass

    def get_daily_stats(self):
        """Get daily trading statistics."""
        return {
            'daily_trades': self.daily_trades,
            'daily_pnl': self.daily_pnl,
            'remaining_trades': TradingConfig.MAX_TRADES_PER_DAY - self.daily_trades
        }

    def print_session_summary(self, force_show: bool = False):
        """
        Print session summary only if there are trades or if force_show is True.
        This prevents showing summary when coins are skipped (no trades executed).
        """
        # Only show summary if there are trades or if explicitly forced
        if self.daily_trades == 0 and not force_show:
            return  # Skip summary if no trades and not forced
        
        runtime = time.time() - self.start_time
        runtime_hours = runtime / 3600
        output_manager.print_static(f"\n{CYAN}{'═'*70}{RESET}")
        output_manager.print_static(f"{CYAN}📊 SESSION SUMMARY{RESET}")
        output_manager.print_static(f"{CYAN}{'═'*70}{RESET}")
        if self.daily_pnl > 0:
            pnl_color = GREEN
            pnl_icon = "💰"
        elif self.daily_pnl < 0:
            pnl_color = RED
            pnl_icon = "💸"
        else:
            pnl_color = YELLOW
            pnl_icon = "➖"
        output_manager.print_static(f"{pnl_color}{pnl_icon} Total P/L: {self.daily_pnl:+.2f}%{RESET}")
        output_manager.print_static(f"📈 Total Trades: {self.daily_trades}")
        output_manager.print_static(f"⏱️  Runtime: {runtime_hours:.1f}h")
        if self.daily_trades > 0:
            avg_pnl = self.daily_pnl / self.daily_trades
            output_manager.print_static(f"📊 Avg P/L: {avg_pnl:+.2f}% per trade")
            win_rate_est = "Unknown"
            if self.daily_pnl > 0:
                win_rate_est = "Profitable Session ✅"
            elif self.daily_pnl < 0:
                win_rate_est = "Loss Session ⚠️"
            output_manager.print_static(f"🎯 Status: {win_rate_est}")
        output_manager.print_static(f"🔄 Remaining: {TradingConfig.MAX_TRADES_PER_DAY - self.daily_trades}/{TradingConfig.MAX_TRADES_PER_DAY} trades")
        # Import portfolio_manager here to avoid circular dependency
        from portfolio import portfolio_manager
        if portfolio_manager.positions:
            output_manager.print_static(f"\n{YELLOW}⚠️  Open Positions: {len(portfolio_manager.positions)}{RESET}")
            for sym, pos in portfolio_manager.positions.items():
                output_manager.print_static(f"  • {sym}: {pos['action']} at ${fmt(pos['price'])}")
        output_manager.print_static(f"{CYAN}{'═'*70}{RESET}")


# Global portfolio manager instance
portfolio_manager = PortfolioManager()

__all__ = ['PortfolioManager', 'portfolio_manager']
