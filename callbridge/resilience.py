"""Circuit breaker + timeout helpers. When a provider keeps failing we stop calling it for a while
instead of making every caller wait for it to time out."""

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

_pool = ThreadPoolExecutor(max_workers=32, thread_name_prefix="cb-timeout")


class CircuitOpen(Exception):
    pass


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 3, reset_after_s: float = 30.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_after_s = reset_after_s
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._opened_at is None:
                return "closed"
            if time.monotonic() - self._opened_at >= self.reset_after_s:
                return "half_open"
            return "open"

    def call[T](self, fn: Callable[[], T]) -> T:
        if self.state == "open":
            raise CircuitOpen(self.name)
        try:
            result = fn()
        except Exception:
            with self._lock:
                self._failures += 1
                if self._failures >= self.failure_threshold or self._opened_at is not None:
                    self._opened_at = time.monotonic()
            raise
        with self._lock:
            self._failures = 0
            self._opened_at = None
        return result


def with_timeout[T](fn: Callable[[], T], seconds: float) -> T:
    future = _pool.submit(fn)
    try:
        return future.result(timeout=seconds)
    except FutureTimeout as e:
        future.cancel()
        raise TimeoutError(f"timed out after {seconds}s") from e


def retry[T](fn: Callable[[], T], attempts: int = 2, backoff_s: float = 0.2,
          retry_on: tuple[type[BaseException], ...] = (Exception,)) -> T:
    for i in range(attempts):
        try:
            return fn()
        except retry_on:
            if i == attempts - 1:
                raise
            time.sleep(backoff_s * (2**i))
    raise AssertionError("unreachable")
