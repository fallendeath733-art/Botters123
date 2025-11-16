"""
Simple logging utilities.
"""
from datetime import datetime
from config.colors import GREEN, RESET


class SimpleLogger:
    """Simple file-based logger for trades and errors."""
    
    def __init__(self):
        self.trade_log = "trades.log"
        self.error_log = "errors.log"
        self.session_start = datetime.now()
        self.stats = {
            'total_trades': 0,
            'total_profit': 0.0,
            'total_loss': 0.0,
            'win_count': 0,
            'loss_count': 0,
            'total_slippage_cost': 0.0,
            'error_count': 0
        }

    def log_trade(self, symbol: str, action: str, price: float, pnl: float = 0,
                  slippage: float = 0, reason: str = ""):
        """Log a trade execution."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.stats['total_trades'] += 1
        if pnl > 0:
            self.stats['win_count'] += 1
            self.stats['total_profit'] += pnl
        elif pnl < 0:
            self.stats['loss_count'] += 1
            self.stats['total_loss'] += abs(pnl)
        if slippage > 0:
            self.stats['total_slippage_cost'] += (slippage / 100) * 100
        with open(self.trade_log, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {action:4} {symbol:15} ${price:10.6f} | "
                   f"P/L: {pnl:+6.2f}% | Slip: {slippage:.2f}% | {reason}\n")

    def log_error(self, context: str, error: str):
        """Log an error."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.stats['error_count'] += 1
        with open(self.error_log, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] {context}: {error}\n")

    def print_summary(self):
        """Print daily summary."""
        win_rate = (self.stats['win_count'] / self.stats['total_trades'] * 100) if self.stats['total_trades'] > 0 else 0
        net_pnl = self.stats['total_profit'] - self.stats['total_loss']
        summary = f"""
{GREEN}╔═══════════════════════════════════════════════════════════════╗{RESET}
{GREEN}║{RESET}                     📊 DAILY SUMMARY                          {GREEN}║{RESET}
{GREEN}╠═══════════════════════════════════════════════════════════════╣{RESET}
{GREEN}║{RESET} Session Start : {self.session_start.strftime("%Y-%m-%d %H:%M:%S")}                        {GREEN}║{RESET}
{GREEN}║{RESET} Total Trades  : {self.stats['total_trades']:3d}                                           {GREEN}║{RESET}
{GREEN}║{RESET} Win / Loss    : {self.stats['win_count']:3d} / {self.stats['loss_count']:3d} ({win_rate:.1f}% win rate)                   {GREEN}║{RESET}
{GREEN}║{RESET} Net P/L       : {net_pnl:+.2f}%                                        {GREEN}║{RESET}
{GREEN}║{RESET} Slippage Cost : ${self.stats['total_slippage_cost']:.2f}                                       {GREEN}║{RESET}
{GREEN}║{RESET} Errors        : {self.stats['error_count']:3d}                                           {GREEN}║{RESET}
{GREEN}╚═══════════════════════════════════════════════════════════════╝{RESET}
"""
        print(summary)
        with open(self.trade_log, 'a', encoding='utf-8') as f:
            f.write("\n" + summary + "\n")


# Global logger instance
simple_logger = SimpleLogger()

__all__ = ['SimpleLogger', 'simple_logger']

