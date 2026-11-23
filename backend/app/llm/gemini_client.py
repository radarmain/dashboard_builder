"""Build the google-genai client from config. The key is never logged.

Always v1beta (the Decisions and Interactions APIs live there). Timeouts are explicit, and the
SDK's default retry loop (5 attempts with backoff, which made a 503 look like a hang) is replaced
by a small attempt count chosen by the caller.
"""

from functools import lru_cache

from google import genai
from google.genai import types

from app.llm.gemini_config import get_gemini_settings


@lru_cache(maxsize=8)
def _build(api_key: str, timeout_s: float, attempts: int) -> genai.Client:
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            api_version="v1beta",
            timeout=int(timeout_s * 1000),  # HttpOptions.timeout is milliseconds
            retry_options=types.HttpRetryOptions(attempts=attempts),
        ),
    )


def get_client(timeout_s: float, attempts: int = 1) -> genai.Client | None:
    """A client with this timeout/attempt count, or None when Gemini is off or has no key."""
    s = get_gemini_settings()
    if not (s.enabled and s.configured and s.api_key):
        return None
    return _build(s.api_key.get_secret_value(), float(timeout_s), int(attempts))
