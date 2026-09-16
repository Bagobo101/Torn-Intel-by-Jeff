import requests
import time
import re


class RateLimitError(Exception):
    """Raised when API rate limit is hit."""
    pass


class HttpClient:
    """
    HTTP client with retry logic, rate limit handling, and multi-key failover.
    Works with ApiKeyManager to handle multiple keys and exponential backoff.
    """

    def __init__(self, timeout=30, key_manager=None):
        self.timeout = timeout
        self.key_manager = key_manager
        self.session = requests.Session()

    ##################################################

    def get(self, url, params=None, max_retries=5, retry_backoff_base=2, pool="default"):
        """
        Make a GET request with automatic retry and multi-key rotation on rate limits / errors.
        
        Args:
            url: URL to request
            params: Query parameters (should include 'key')
            max_retries: Max retry attempts
            retry_backoff_base: Base for exponential backoff
            pool: Key pool name to rotate within
            
        Returns:
            JSON response
            
        Raises:
            RateLimitError: If rate limit persists after all retries
            requests.HTTPError: For other HTTP errors
        """
        attempt = 0
        params = dict(params or {})
        
        retry_schedule = [10, 20, 30, 60]
        if self.key_manager and hasattr(self.key_manager.settings, "rate_limit_retry_schedule"):
            retry_schedule = self.key_manager.settings.rate_limit_retry_schedule

        while attempt < max_retries:
            current_key = params.get("key")
            try:
                response = self.session.get(
                    url,
                    params=params,
                    timeout=self.timeout
                )
                response.raise_for_status()
                
                data = response.json()
                
                # Check for Torn API errors in response
                if isinstance(data, dict) and "error" in data:
                    error_code = data["error"].get("code")
                    error_msg = data["error"].get("error", "Unknown error")
                    
                    # Error code 5 = burst rate limit, 14 = daily read limit
                    if error_code in (5, 14):
                        wait_seconds = None
                        wait_match = re.search(r"(\d+)\s*second", str(error_msg), re.IGNORECASE)
                        if wait_match:
                            wait_seconds = int(wait_match.group(1))

                        attempt += 1
                        idx = min(attempt - 1, len(retry_schedule) - 1)
                        backoff = wait_seconds or retry_schedule[idx]

                        if self.key_manager and current_key:
                            self.key_manager.record_rate_limit(current_key, wait_seconds=backoff)

                        if attempt >= max_retries:
                            raise RateLimitError(
                                f"Rate limited after {max_retries} attempts: {error_msg}"
                            )

                        # If key manager has another available key in the pool, fail over immediately!
                        if self.key_manager and self.key_manager.has_available_key(pool=pool):
                            next_key = self.key_manager.get_next_key(pool=pool, skip_rate_limited=True)
                            if next_key != current_key:
                                params["key"] = next_key
                                continue

                        # All keys in pool are limited, wait as last resort
                        print(
                            f"Rate limit (code {error_code}) on all pool keys, backing off {backoff}s "
                            f"(attempt {attempt}/{max_retries})"
                        )
                        time.sleep(backoff)
                        continue
                    
                    # Other Torn API errors - record failure and return
                    if self.key_manager and current_key:
                        self.key_manager.record_failure(current_key, error_code=error_code)
                    return data
                
                # Success
                if self.key_manager and current_key:
                    self.key_manager.record_success(current_key)
                
                return data
                
            except requests.exceptions.Timeout:
                attempt += 1
                if self.key_manager and current_key:
                    self.key_manager.record_failure(current_key)
                if attempt >= max_retries:
                    raise
                
                if self.key_manager and self.key_manager.has_available_key(pool=pool):
                    next_key = self.key_manager.get_next_key(pool=pool, skip_rate_limited=True)
                    if next_key != current_key:
                        params["key"] = next_key
                        continue

                backoff = retry_backoff_base ** attempt
                print(f"Timeout, backing off {backoff}s (attempt {attempt}/{max_retries})")
                time.sleep(backoff)
                
            except requests.exceptions.RequestException as e:
                attempt += 1
                if self.key_manager and current_key:
                    self.key_manager.record_failure(current_key)
                if attempt >= max_retries:
                    raise
                
                if self.key_manager and self.key_manager.has_available_key(pool=pool):
                    next_key = self.key_manager.get_next_key(pool=pool, skip_rate_limited=True)
                    if next_key != current_key:
                        params["key"] = next_key
                        continue

                backoff = retry_backoff_base ** attempt
                print(f"Request error: {e}, backing off {backoff}s (attempt {attempt}/{max_retries})")
                time.sleep(backoff)