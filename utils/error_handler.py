"""
Error handling utilities.
"""
import time
from typing import Dict
from config.colors import RED, YELLOW, RESET


class ErrorHandler:
    """Centralized error handler with threshold-based shutdown."""
    
    def __init__(self):
        self.error_count = 0
        self.last_error_time = 0
        self.error_threshold = 10
        self.time_window = 300

    def should_continue(self) -> bool:
        """Check if bot should continue running based on error count."""
        current_time = time.time()
        if current_time - self.last_error_time > self.time_window:
            self.error_count = 0
            self.last_error_time = current_time
        return self.error_count < self.error_threshold

    def record_error(self, error_type: str, error_msg: str, context: Dict = None):
        """Record an error and check if shutdown is needed."""
        self.error_count += 1
        self.last_error_time = time.time()
        error_info = {
            'type': error_type,
            'message': error_msg,
            'timestamp': time.time(),
            'context': context or {},
            'count': self.error_count
        }
        print(f"{RED}❌ ERROR [{error_type}]: {error_msg}{RESET}")
        if context:
            print(f"{YELLOW}   Context: {context}{RESET}")
        if self.error_count >= self.error_threshold:
            print(f"{RED}🚨 EMERGENCY SHUTDOWN: Too many errors ({self.error_count}){RESET}")

    def get_error_stats(self):
        """Get current error statistics."""
        return {
            'error_count': self.error_count,
            'last_error_time': self.last_error_time,
            'should_continue': self.should_continue()
        }


# Global error handler instance
error_handler = ErrorHandler()

__all__ = ['ErrorHandler', 'error_handler']

