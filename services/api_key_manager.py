"""
services/api_key_manager.py

Manages multiple API keys for rate limit distribution, rotation, and multi-faction pools.
Handles key cycling, rate limit tracking, failover, and retry logic.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any


class ApiKeyManager:
    """
    Manage multiple API keys with automatic rotation, multi-faction pools, and failover.
    
    Features:
    - Faction-specific key pools (e.g. GTS, GTH) and global failover pools
    - Cycle through keys to distribute API calls
    - Track rate limit status per key
    - Automatic key failover and recovery
    """

    def __init__(self, api_keys=None, settings=None, logger=None):
        """
        Args:
            api_keys: Optional legacy list of API key strings
            settings: Settings object
            logger: Logger instance
        """
        self.settings = settings
        self.logger = logger
        
        # Legacy list of keys
        self.api_keys = list(api_keys or (settings.api_keys if settings else []))
        
        # Build pools
        self.pools: dict[str, list[str]] = {}
        self.pool_indices: dict[str, int] = defaultdict(int)

        if settings:
            for tag, faction in settings.factions.items():
                if faction.api_keys:
                    self.pools[tag.upper()] = list(faction.api_keys)
            if settings.global_api_keys:
                self.pools["GLOBAL"] = list(settings.global_api_keys)

        # Ensure default pool exists
        if "GTS" not in self.pools and self.api_keys:
            self.pools["GTS"] = list(self.api_keys)

        # Collect ALL unique keys
        all_keys = []
        for p_keys in self.pools.values():
            for k in p_keys:
                if k not in all_keys:
                    all_keys.append(k)
        if not all_keys and self.api_keys:
            all_keys = list(self.api_keys)
        self.pools["ALL"] = all_keys

        # Legacy current key index
        self.current_key_index = 0
        
        # Track rate limit status per key
        # key -> {"rate_limited": bool, "retry_after": timestamp, "failed_attempts": int, ...}
        self.key_status = defaultdict(lambda: {
            "rate_limited": False,
            "retry_after": 0.0,
            "failed_attempts": 0,
            "total_requests": 0,
            "total_rate_limits": 0,
        })

    ########################################################

    def _resolve_pool_keys(self, pool: str | None = "default") -> tuple[str, list[str]]:
        if not pool or pool == "default":
            tag = self.settings.default_faction.tag if self.settings and self.settings.default_faction else "GTS"
        else:
            tag = str(pool).strip().upper()

        if tag in self.pools and self.pools[tag]:
            return tag, self.pools[tag]

        if self.settings:
            faction = self.settings.get_faction(tag)
            if faction and faction.api_keys:
                return faction.tag, faction.api_keys

        if tag == "GLOBAL" and "GLOBAL" in self.pools and self.pools["GLOBAL"]:
            return "GLOBAL", self.pools["GLOBAL"]

        # Fallback to ALL or legacy keys
        return "ALL", self.pools.get("ALL", self.api_keys)

    ########################################################

    def has_available_key(self, pool: str | None = "default") -> bool:
        """Check if any key in the specified pool is currently not rate limited."""
        _, keys = self._resolve_pool_keys(pool)
        if not keys:
            return False
        now = time.time()
        for k in keys:
            status = self.key_status[k]
            if not status["rate_limited"] or now >= status["retry_after"]:
                return True
        return False

    ########################################################

    def get_pool_keys(self, pool: str | None = "default") -> list[str]:
        """Return the list of keys configured for a pool."""
        _, keys = self._resolve_pool_keys(pool)
        return list(keys)

    ########################################################

    def get_next_key(self, pool: str | None = "default", skip_rate_limited: bool = True, fallback_to_global: bool = False) -> str:
        """
        Get the next API key for the pool, optionally skipping rate-limited ones.
        Uses round-robin rotation.
        """
        pool_tag, keys = self._resolve_pool_keys(pool)
        if not keys:
            return self.settings.api_key if self.settings else ""

        start_index = self.pool_indices[pool_tag] % len(keys)
        curr = start_index

        now = time.time()
        while True:
            key = keys[curr]
            curr = (curr + 1) % len(keys)
            self.pool_indices[pool_tag] = curr

            if skip_rate_limited:
                status = self.key_status[key]
                if status["rate_limited"]:
                    if now < status["retry_after"]:
                        if curr == start_index:
                            # All keys in pool are currently rate limited
                            if fallback_to_global and pool_tag != "GLOBAL" and "GLOBAL" in self.pools and self.pools["GLOBAL"]:
                                if self.has_available_key("GLOBAL"):
                                    return self.get_next_key(pool="GLOBAL", skip_rate_limited=True, fallback_to_global=False)

                            # Wait for earliest key in pool
                            earliest_key = min(keys, key=lambda k: self.key_status[k]["retry_after"])
                            wait_time = max(0.1, self.key_status[earliest_key]["retry_after"] - time.time())
                            if self.logger:
                                self.logger.warning(
                                    f"All API keys in pool '{pool_tag}' rate limited, waiting {wait_time:.1f}s"
                                )
                            time.sleep(wait_time + 0.1)
                            self.key_status[earliest_key]["rate_limited"] = False
                            return earliest_key
                        continue
                    else:
                        status["rate_limited"] = False
                        if self.logger:
                            self.logger.info(f"Key {key[:8]}... recovered from rate limit")

            return key

    ########################################################

    def record_success(self, key):
        """Record a successful API call with this key."""
        status = self.key_status[key]
        status["total_requests"] += 1
        status["failed_attempts"] = 0

    ########################################################

    def record_rate_limit(self, key, wait_seconds=None):
        """
        Record a rate limit error for a key.
        """
        status = self.key_status[key]
        status["total_rate_limits"] += 1
        status["rate_limited"] = True
        status["failed_attempts"] += 1

        if wait_seconds:
            status["retry_after"] = time.time() + wait_seconds
            if self.logger:
                self.logger.warning(
                    f"Rate limited (key {key[:8]}...), waiting {wait_seconds}s"
                )
        else:
            base = getattr(self.settings, "retry_backoff_base", 2) if self.settings else 2
            backoff = base ** status["failed_attempts"]
            status["retry_after"] = time.time() + backoff
            if self.logger:
                self.logger.warning(
                    f"Rate limited (key {key[:8]}...), backing off {backoff}s (attempt {status['failed_attempts']})"
                )

    ########################################################

    def record_failure(self, key, error_code=None):
        """Record a request failure."""
        status = self.key_status[key]
        status["failed_attempts"] += 1
        status["total_requests"] += 1

    ########################################################

    def get_status(self, pool: str | None = None):
        """Get summary of all keys (or pool keys) and their status."""
        _, keys = self._resolve_pool_keys(pool) if pool else ("ALL", self.pools.get("ALL", self.api_keys))
        summary = []
        now = time.time()
        for key in keys:
            status = self.key_status[key]
            is_limited = status["rate_limited"] and now < status["retry_after"]
            time_until_retry = max(0, status["retry_after"] - now)

            summary.append({
                "key": f"{key[:12]}...",
                "rate_limited": is_limited,
                "time_until_retry": f"{time_until_retry:.1f}s" if is_limited else "N/A",
                "total_requests": status["total_requests"],
                "total_rate_limits": status["total_rate_limits"],
            })

        return summary

    ########################################################

    def log_status(self, pool: str | None = None):
        """Log current status of all API keys."""
        status = self.get_status(pool=pool)
        available_keys = sum(1 for s in status if not s["rate_limited"])

        if self.logger:
            pool_label = f" (pool {pool})" if pool else ""
            self.logger.info(f"API Key Status{pool_label}: {available_keys}/{len(status)} available")
            for s in status:
                if s["rate_limited"]:
                    self.logger.info(
                        f"  Key {s['key']} - RATE LIMITED (retry in {s['time_until_retry']})"
                    )
                else:
                    self.logger.info(
                        f"  Key {s['key']} - {s['total_requests']} req, {s['total_rate_limits']} limits"
                    )
