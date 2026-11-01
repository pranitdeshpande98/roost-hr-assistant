"""LLM errors, split by what the caller should do about them."""


class LLMError(Exception):
    """Base class for every LLM failure."""


class LLMUnavailableError(LLMError):
    """Transient: timeout, connection failure, rate limit or provider 5xx. Try the fallback."""


class LLMRequestError(LLMError):
    """Our fault or config: bad request, bad key, unknown model. Retrying will not help."""


class LLMResponseError(LLMError):
    """The provider answered, but not in a shape we can use (e.g. tool arguments not JSON)."""
