import logging
from mistralai.client.sdk import Mistral
from config import MISTRAL_API_KEY, MISTRAL_MODEL, MISTRAL_AGENT_ID, MISTRAL_SDK_TIMEOUT

logger = logging.getLogger(__name__)
_client: Mistral | None = None

_AGENT_RETRIES = 2  # total attempts for agent_chat before giving up


def _get_client() -> Mistral:
    global _client
    if _client is None:
        _client = Mistral(api_key=MISTRAL_API_KEY, timeout_ms=MISTRAL_SDK_TIMEOUT)
    return _client


def simple_chat(messages: list, system: str = None, temperature: float | None = None) -> str:
    """Plain chat completion. Pass temperature=0 for deterministic, fact-bound output."""
    full = []
    if system:
        full.append({"role": "system", "content": system})
    full.extend(messages)
    kwargs = {"model": MISTRAL_MODEL, "messages": full}
    if temperature is not None:
        kwargs["temperature"] = temperature
    response = _get_client().chat.complete(**kwargs)
    return response.choices[0].message.content or ""


def agent_chat(message: str) -> str:
    """Send a message to the Mistral agent with retry on transient errors."""
    last_exc: Exception | None = None
    for attempt in range(1, _AGENT_RETRIES + 1):
        try:
            response = _get_client().beta.conversations.start(
                agent_id=MISTRAL_AGENT_ID,
                inputs=message,
            )
            for entry in reversed(response.outputs):
                if hasattr(entry, "content"):
                    for chunk in entry.content:
                        if hasattr(chunk, "text"):
                            return chunk.text
            return ""
        except Exception as exc:
            last_exc = exc
            logger.warning("agent_chat attempt %d/%d failed: %s", attempt, _AGENT_RETRIES, exc)

    raise last_exc
