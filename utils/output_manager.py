"""
Output management for terminal display.
"""
import time
import shutil
import re
from config.colors import CYAN, GREEN, RESET


class OutputManager:
    """Manages terminal output with live update support."""
    
    def __init__(self):
        self.live_update_active = False
        self.message_count = 0
        self.start_time = time.time()
        self.last_message_length = 0
        self.terminal_width = self._get_terminal_width()

    def _get_terminal_width(self):
        """Get terminal width with fallback."""
        try:
            width = shutil.get_terminal_size().columns
            return max(80, width - 5)
        except:
            return 80

    def start_live_update(self):
        """Start live update mode."""
        self.live_update_active = True
        self.last_message_length = 0
        self.terminal_width = self._get_terminal_width()

    def stop_live_update(self):
        """Stop live update mode."""
        if self.live_update_active:
            self.live_update_active = False
            print("\033[2K\r", end="", flush=True)
            self.last_message_length = 0

    def print_live(self, message: str):
        """Print live update message (overwrites previous line)."""
        if self.live_update_active:
            # Calculate message length (without ANSI codes) for proper padding
            clean_message = re.sub(r'\033\[[0-9;]*m', '', message)
            message_length = len(clean_message)
            
            # Clear line
            print("\033[2K\r", end="", flush=True)
            
            # Print message with padding to clear any leftover characters (reduce flickering)
            max_length = max(self.last_message_length, message_length)
            padding = " " * max(0, max_length - message_length)
            print(f"{message}{padding}", end="", flush=True)
            
            self.last_message_length = message_length
            self.message_count += 1

    def print_static(self, message: str):
        """Print static message (new line)."""
        if self.live_update_active:
            print("\033[2K\r", end="", flush=True)
            self.last_message_length = 0
            print()
        print(message)

    def print_separator(self, char: str = "─", color: str = CYAN, width: int = None):
        """Print separator line."""
        if width is None:
            width = 80
        separator = char * width
        self.print_static(f"{color}{separator}{RESET}")

    def print_header(self, title: str, color: str = GREEN, width: int = None):
        """Print header with title."""
        if width is None:
            width = 80
        padding = (width - len(title) - 4) // 2
        header = f"{color}{'='*width}{RESET}\n{color}{' '*padding} {title} {' '*padding}{RESET}\n{color}{'='*width}{RESET}"
        self.print_static(header)

    def get_performance_stats(self):
        """Get output performance statistics."""
        elapsed = time.time() - self.start_time
        return {
            "messages_per_second": self.message_count / elapsed if elapsed > 0 else 0,
            "total_messages": self.message_count,
            "elapsed_time": elapsed
        }


# Global output manager instance
output_manager = OutputManager()

__all__ = ['OutputManager', 'output_manager']

