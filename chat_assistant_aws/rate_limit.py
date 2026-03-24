"""In-memory sliding window rate limiter (per-IP)."""

import time
from collections import defaultdict

MAX_REQUESTS = 5
WINDOW_SECONDS = 60


class RateLimiter:
    def __init__(self, max_requests: int = MAX_REQUESTS, window: float = WINDOW_SECONDS):
        self.max_requests = max_requests
        self.window = window
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, ip: str) -> bool:
        """Return True if the request is allowed, False if rate limit exceeded."""
        now = time.time()
        cutoff = now - self.window

        # Clean expired entries for this IP
        self._requests[ip] = [t for t in self._requests[ip] if t > cutoff]

        if len(self._requests[ip]) >= self.max_requests:
            return False

        self._requests[ip].append(now)
        return True

    def cleanup(self) -> None:
        """Remove IPs with no recent requests to prevent memory leaks."""
        now = time.time()
        cutoff = now - self.window
        empty_ips = [
            ip for ip, times in self._requests.items()
            if not any(t > cutoff for t in times)
        ]
        for ip in empty_ips:
            del self._requests[ip]


rate_limiter = RateLimiter()
