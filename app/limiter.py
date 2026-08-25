import asyncio
import time


class TokenBucket:
    """Async token bucket: `rpm` tokens refill per minute, up to `capacity`."""

    def __init__(self, rpm: float, capacity: float):
        self.rate = rpm / 60  # tokens per second
        self.capacity = capacity
        self.tokens = capacity
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, amount: float = 1) -> float:
        """Block until ``amount`` tokens are available and return seconds waited."""
        if amount <= 0:
            return 0
        if amount > self.capacity:
            raise ValueError("Requested token amount exceeds bucket capacity")
        start = time.monotonic()
        while True:
            async with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.last_refill) * self.rate)
                self.last_refill = now
                if self.tokens >= amount:
                    self.tokens -= amount
                    return time.monotonic() - start
                wait = (amount - self.tokens) / self.rate
            await asyncio.sleep(wait)

    async def refund(self, amount: float) -> None:
        if amount <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last_refill) * self.rate + amount)
            self.last_refill = now

    async def level(self) -> float:
        async with self._lock:
            now = time.monotonic()
            return min(self.capacity, self.tokens + (now - self.last_refill) * self.rate)

    async def reconfigure(self, *, rpm: float, capacity: float) -> None:
        """Apply new limits without dropping queued callers or replacing the lock."""
        async with self._lock:
            now = time.monotonic()
            current = min(self.capacity, self.tokens + (now - self.last_refill) * self.rate)
            self.rate = rpm / 60
            self.capacity = capacity
            self.tokens = min(capacity, current)
            self.last_refill = now
