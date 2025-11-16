"""
Circuit breaker pattern implementation.
"""
import time
import threading
from config.colors import GREEN, RED, RESET


class CircuitBreaker:
    """Circuit breaker for API calls with failure threshold."""
    
    def __init__(self, max_failures=5, reset_timeout=60):
        self.failures = 0
        self.max_failures = max_failures
        self.reset_timeout = reset_timeout
        self.last_failure_time = 0
        self.lock = threading.Lock()

    def can_execute(self):
        """Check if execution is allowed."""
        with self.lock:
            if self.failures >= self.max_failures:
                if time.time() - self.last_failure_time > self.reset_timeout:
                    self.failures = 0
                    print(f"{GREEN}✅ Circuit breaker reset{RESET}")
                else:
                    return False
            return True

    def record_failure(self):
        """Record a failure."""
        with self.lock:
            self.failures += 1
            self.last_failure_time = time.time()
            print(f"{RED}🚨 Circuit breaker: {self.failures}/{self.max_failures} failures{RESET}")

    def record_success(self):
        """Record a success (reduces failure count)."""
        with self.lock:
            if self.failures > 0:
                self.failures = max(0, self.failures - 1)
                print(f"{GREEN}✅ Circuit breaker: Recovery {self.failures}/{self.max_failures}{RESET}")


__all__ = ['CircuitBreaker']

