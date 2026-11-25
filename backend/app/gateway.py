import logging
import time
from typing import Any, Optional

import litellm
from litellm.caching.caching import Cache

from app.config import KeyEntry, Settings

logger = logging.getLogger(__name__)

VALID_SIMULATED_STATUSES = {429, 500, 502, 503}


class AllProvidersUnavailableError(Exception):
    """Raised when every configured API key in the pool fails."""


def parse_simulate_failure(
    header_value: Optional[str], enabled: bool
) -> dict[str, int]:
    """Parse X-Simulate-Failure header (e.g. 'key_1=429,key_2=500' or '*=429')."""
    if not enabled or not header_value:
        return {}

    rules: dict[str, int] = {}
    for raw_pair in header_value.split(","):
        pair = raw_pair.strip()
        if not pair or "=" not in pair:
            continue
        key_part, status_part = pair.split("=", 1)
        key_id = key_part.strip()
        try:
            status_code = int(status_part.strip())
        except ValueError:
            continue
        if key_id and status_code in VALID_SIMULATED_STATUSES:
            rules[key_id] = status_code
    return rules


def _raise_simulated_error(status_code: int, key_id: str, model: str) -> None:
    """Raise a LiteLLM exception matching the simulated HTTP status code."""
    provider = model.split("/", 1)[0] if "/" in model else "custom"
    msg = f"Simulated HTTP {status_code} failure for {key_id}"
    if status_code == 429:
        raise litellm.RateLimitError(message=msg, llm_provider=provider, model=model)
    if status_code == 500:
        raise litellm.InternalServerError(message=msg, llm_provider=provider, model=model)
    if status_code == 502:
        raise litellm.BadGatewayError(message=msg, llm_provider=provider, model=model)
    raise litellm.ServiceUnavailableError(message=msg, llm_provider=provider, model=model)


def _extract_reply_and_usage(response: Any) -> tuple[str, int, int, str, bool]:
    """Extract reply text, token counts, model name, and cache-hit flag from a LiteLLM response."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise ValueError("Empty choices returned by LLM provider")

    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Empty reply content returned by LLM provider")

    usage = getattr(response, "usage", None)
    if isinstance(usage, dict):
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
    else:
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

    hidden = getattr(response, "_hidden_params", None) or {}
    cache_hit = bool(hidden.get("cache_hit", False))
    response_model = getattr(response, "model", None) or ""

    return content, input_tokens, output_tokens, response_model, cache_hit


class LLMGateway:
    """Gateway wrapping LiteLLM with ordered key failover, failure simulation, and response caching."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.key_pool: tuple[KeyEntry, ...] = settings.key_pool
        self.cache_enabled = False
        self._init_cache()

    def _init_cache(self) -> None:
        litellm.suppress_debug_info = True
        litellm.drop_params = True
        cache_type = self.settings.cache_type
        if cache_type not in ("local", "disk"):
            cache_type = "local"
        litellm.cache = Cache(type=cache_type)
        self.cache_enabled = True

    def generate_reply(
        self,
        messages: list[dict[str, str]],
        simulate_failure_header: Optional[str] = None,
    ) -> dict[str, Any]:
        """Call LiteLLM with caching and automatic failover across the configured key pool."""
        if not self.key_pool:
            raise AllProvidersUnavailableError("No API keys configured")

        simulated_rules = parse_simulate_failure(
            simulate_failure_header,
            self.settings.enable_failure_simulation,
        )
        bypass_cache = bool(simulated_rules)

        start = time.perf_counter()
        fallback_attempts = 0

        for entry in self.key_pool:
            try:
                sim_status = simulated_rules.get("*") or simulated_rules.get(entry.key_id)
                if sim_status is not None:
                    _raise_simulated_error(sim_status, entry.key_id, entry.model)

                completion_kwargs: dict[str, Any] = {
                    "model": entry.model,
                    "messages": messages,
                    "api_key": entry.api_key,
                    "timeout": self.settings.llm_timeout_seconds,
                    "num_retries": 0,
                }
                if bypass_cache:
                    completion_kwargs["cache"] = {"no-cache": True, "no-store": True}

                response = litellm.completion(**completion_kwargs)
                reply, input_tokens, output_tokens, _, cache_hit = (
                    _extract_reply_and_usage(response)
                )
                latency_ms = max(1, int(round((time.perf_counter() - start) * 1000)))

                if cache_hit:
                    return {
                        "reply": reply,
                        "metadata": {
                            "resolution_source": "LLM_CACHE",
                            "model": entry.model,
                            "latency_ms": latency_ms,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "key_id": None,
                            "fallback_attempts": 0,
                        },
                    }

                return {
                    "reply": reply,
                    "metadata": {
                        "resolution_source": "LIVE_INFERENCE",
                        "model": entry.model,
                        "latency_ms": latency_ms,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "key_id": entry.key_id,
                        "fallback_attempts": fallback_attempts,
                    },
                }

            except Exception as exc:
                fallback_attempts += 1
                logger.warning(
                    "Key %s failed for model %s (%s). Failing over.",
                    entry.key_id,
                    entry.model,
                    type(exc).__name__,
                )
                continue

        raise AllProvidersUnavailableError("All providers unavailable")
