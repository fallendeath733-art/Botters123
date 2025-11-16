"""
Performance monitoring utilities.
"""
import time
from utils.helpers import safe_division
from config.colors import MAGENTA, CYAN, RESET


class PerformanceMonitor:
    """Monitor and track performance metrics."""
    
    def __init__(self):
        self.metrics = {
            'api_calls': 0,
            'analysis_count': 0,
            'trades_executed': 0,
            'errors_encountered': 0,
            'start_time': time.time()
        }
        self.analysis_times = []
        self.api_response_times = []
        self.trade_performance = []
        self.win_rate = 0.0
        self.avg_profit = 0.0
        self.max_drawdown = 0.0

    def record_api_call(self, duration: float):
        """Record API call duration."""
        self.metrics['api_calls'] += 1
        self.api_response_times.append(duration)
        if len(self.api_response_times) > 100:
            self.api_response_times.pop(0)

    def record_analysis(self, duration: float):
        """Record analysis duration."""
        self.metrics['analysis_count'] += 1
        self.analysis_times.append(duration)
        if len(self.analysis_times) > 50:
            self.analysis_times.pop(0)

    def record_trade(self):
        """Record a trade execution."""
        self.metrics['trades_executed'] += 1

    def record_error(self):
        """Record an error."""
        self.metrics['errors_encountered'] += 1

    def record_trade_performance(self, entry_price: float, exit_price: float,
                               position_type: str, duration: float):
        """Record trade performance metrics."""
        pnl_percent = safe_division((exit_price - entry_price), entry_price, 0.0) * 100
        if position_type == "SELL":
            pnl_percent = -pnl_percent
        trade_data = {
            'entry_price': entry_price,
            'exit_price': exit_price,
            'pnl_percent': pnl_percent,
            'position_type': position_type,
            'duration_minutes': duration / 60,
            'timestamp': time.time()
        }
        self.trade_performance.append(trade_data)
        winning_trades = [t for t in self.trade_performance if t['pnl_percent'] > 0]
        self.win_rate = len(winning_trades) / len(self.trade_performance) if self.trade_performance else 0
        if self.trade_performance:
            self.avg_profit = sum(t['pnl_percent'] for t in self.trade_performance) / len(self.trade_performance)

    def get_trading_performance(self):
        """Get trading performance statistics."""
        if not self.trade_performance:
            return {}
        recent_trades = self.trade_performance[-10:]
        recent_win_rate = len([t for t in recent_trades if t['pnl_percent'] > 0]) / len(recent_trades) if recent_trades else 0
        return {
            'total_trades': len(self.trade_performance),
            'win_rate': f"{self.win_rate * 100:.1f}%",
            'recent_win_rate': f"{recent_win_rate * 100:.1f}%",
            'avg_profit': f"{self.avg_profit:.2f}%",
            'best_trade': f"{max(t['pnl_percent'] for t in self.trade_performance):.2f}%" if self.trade_performance else "0%",
            'worst_trade': f"{min(t['pnl_percent'] for t in self.trade_performance):.2f}%" if self.trade_performance else "0%"
        }

    def get_performance_report(self):
        """Get comprehensive performance report."""
        current_time = time.time()
        elapsed_hours = (current_time - self.metrics['start_time']) / 3600
        avg_api_time = sum(self.api_response_times) / len(self.api_response_times) if self.api_response_times else 0
        avg_analysis_time = sum(self.analysis_times) / len(self.analysis_times) if self.analysis_times else 0
        trades_per_hour = self.metrics['trades_executed'] / elapsed_hours if elapsed_hours > 0 else 0
        api_calls_per_hour = self.metrics['api_calls'] / elapsed_hours if elapsed_hours > 0 else 0
        trading_perf = self.get_trading_performance()
        report = {
            'session_duration_hours': f"{elapsed_hours:.2f}",
            'trades_executed': self.metrics['trades_executed'],
            'trades_per_hour': f"{trades_per_hour:.2f}",
            'api_calls_total': self.metrics['api_calls'],
            'api_calls_per_hour': f"{api_calls_per_hour:.2f}",
            'avg_api_response_time_ms': f"{avg_api_time * 1000:.1f}",
            'avg_analysis_time_ms': f"{avg_analysis_time * 1000:.1f}",
            'analysis_count': self.metrics['analysis_count'],
            'errors_encountered': self.metrics['errors_encountered']
        }
        report.update(trading_perf)
        return report

    def print_performance_report(self):
        """Print performance report to console."""
        report = self.get_performance_report()
        print(f"\n{MAGENTA}{'📊 PERFORMANCE REPORT ':=^80}{RESET}")
        print(f"{CYAN}Session Duration:{RESET} {report['session_duration_hours']} hours")
        print(f"{CYAN}Trading Activity:{RESET} {report['trades_executed']} trades ({report['trades_per_hour']}/hour)")
        print(f"{CYAN}API Performance:{RESET} {report['api_calls_total']} calls ({report['api_calls_per_hour']}/hour)")
        print(f"{CYAN}Response Times:{RESET} API: {report['avg_api_response_time_ms']}ms, Analysis: {report['avg_analysis_time_ms']}ms")
        print(f"{CYAN}Analysis Count:{RESET} {report['analysis_count']}")
        print(f"{CYAN}Error Rate:{RESET} {report['errors_encountered']} errors")
        if self.trade_performance:
            print(f"{CYAN}Trading Performance:{RESET}")
            print(f"  Win Rate: {report['win_rate']} | Recent: {report['recent_win_rate']}")
            print(f"  Avg Profit: {report['avg_profit']} | Best: {report['best_trade']} | Worst: {report['worst_trade']}")
        print(f"{MAGENTA}{'='*80}{RESET}")


# Global performance monitor instance
performance_monitor = PerformanceMonitor()

__all__ = ['PerformanceMonitor', 'performance_monitor']

