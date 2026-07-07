import asyncio
import logging

from backend.utils.redis_pool import get_redis

logger = logging.getLogger(__name__)


class BruteForceProtection:
    """Redis-based brute-force protection for login endpoint.

    Two-layer defense:
      1) IP sliding window — blocks after N attempts in W seconds from one IP.
      2) Account lockout     — locks user account after T failed attempts (duration D).

    Fail-open: any Redis error allows the request through to avoid locking out
    legitimate users when Redis is down.
    """

    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        lockout_threshold: int,
        lockout_duration: int,
    ):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_threshold = lockout_threshold
        self.lockout_duration = lockout_duration

    # -- key helpers ----------------------------------------------------------

    def _ip_key(self, ip: str) -> str:
        return f"bf:login:ip:{ip}"

    def _user_fails_key(self, username: str) -> str:
        return f"bf:login:user_fails:{username}"

    def _lockout_key(self, username: str) -> str:
        return f"bf:lockout:{username}"

    def _ip_lockout_count_key(self, ip: str) -> str:
        """Track how many times this IP has triggered account lockouts."""
        return f"bf:ip_lockout_count:{ip}"

    # -- public API -----------------------------------------------------------

    async def is_account_locked(self, username: str) -> bool:
        """Return True if the account is currently under lockout."""
        try:
            r = await get_redis()
            remaining = await r.ttl(self._lockout_key(username))
            return remaining > 0
        except Exception:
            logger.exception("Brute-force lockout check failed — fail-open")
            return False

    async def get_lockout_remaining(self, username: str) -> int:
        """Return seconds until lockout expires (0 if not locked)."""
        try:
            r = await get_redis()
            remaining = await r.ttl(self._lockout_key(username))
            return max(remaining, 0)
        except Exception:
            return 0

    async def record_failed_attempt(self, ip: str, username: str) -> dict:
        """Record a failed login attempt.

        Returns {\"blocked\": bool, \"reason\": str | None}.
        When blocked is True the caller should respond with HTTP 429.
        """
        try:
            r = await get_redis()
            now = asyncio.get_event_loop().time()

            # --- Layer 1: IP sliding window ----------------------------------
            ip_key = self._ip_key(ip)
            pipe = r.pipeline()
            pipe.zremrangebyscore(ip_key, 0, now - self.window_seconds)
            pipe.zcard(ip_key)
            results = await pipe.execute()
            ip_count = results[1]

            if ip_count >= self.max_attempts:
                logger.warning(
                    f"IP {ip} blocked after {ip_count} login attempts "
                    f"in {self.window_seconds}s window"
                )
                return {
                    "blocked": True,
                    "reason": (
                        f"Too many attempts from this IP. "
                        f"Try again in {self.window_seconds} seconds."
                    ),
                }

            # --- Record attempt on both IP and user keys --------------------
            pipe = r.pipeline()
            pipe.zadd(ip_key, {str(now): now})
            pipe.expire(ip_key, self.window_seconds + 1)
            pipe.incr(self._user_fails_key(username))
            results = await pipe.execute()

            total_user_fails = results[1]

            # --- Layer 2: account lockout (progressive per IP) ---------------
            if total_user_fails >= self.lockout_threshold:
                # Progressive duration: each lockout from same IP doubles time.
                # 1st → base, 2nd → ×2, 3rd → ×4, capped at ×8 (60 min for 900s base).
                count_key = self._ip_lockout_count_key(ip)
                prev_count = await r.get(count_key)
                lockout_count = int(prev_count) + 1 if prev_count else 1

                multiplier = min(2 ** (lockout_count - 1), 8)
                actual_duration = self.lockout_duration * multiplier

                # Persist counter with long TTL so it survives across reboots.
                await r.setex(count_key, 3600, lockout_count)

                await r.setex(
                    self._lockout_key(username),
                    actual_duration,
                    "locked",
                )
                logger.warning(
                    f"Account '{username}' locked (#{lockout_count} for IP {ip}) "
                    f"after {total_user_fails} failed attempts — "
                    f"duration {actual_duration}s ({multiplier}× base)"
                )
                return {
                    "blocked": True,
                    "reason": (
                        f"Account temporarily locked. "
                        f"Try again in {actual_duration // 60} minutes."
                    ),
                }

            logger.warning(f"Failed login attempt: user={username!r} ip={ip}")
            return {"blocked": False, "reason": None}

        except Exception:
            logger.exception("Brute-force protection failed — fail-open")
            return {"blocked": False, "reason": None}

    async def record_success(self, username: str) -> None:
        """Reset all counters on successful login."""
        try:
            r = await get_redis()
            pipe = r.pipeline()
            pipe.delete(self._user_fails_key(username))
            pipe.delete(self._lockout_key(username))
            await pipe.execute()
        except Exception:
            logger.exception("Failed to reset brute-force counters")

    async def reset_ip_lockout_count(self, ip: str) -> None:
        """Reset progressive lockout counter for an IP (called on success)."""
        try:
            r = await get_redis()
            await r.delete(self._ip_lockout_count_key(ip))
        except Exception:
            logger.exception("Failed to reset IP lockout count")


# -- singleton ---------------------------------------------------------------

_brute_force_protector: BruteForceProtection | None = None


def get_brute_force_protector() -> BruteForceProtection:
    """Lazy-initialise the protector from settings."""
    global _brute_force_protector
    if _brute_force_protector is None:
        from backend.utils.config import settings

        _brute_force_protector = BruteForceProtection(
            max_attempts=settings.login_max_attempts,
            window_seconds=settings.login_window_seconds,
            lockout_threshold=settings.login_lockout_threshold,
            lockout_duration=settings.login_lockout_duration,
        )
    return _brute_force_protector
