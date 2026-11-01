"""Provider registry and API-key settings for OpenAI-compatible endpoints."""

from dataclasses import dataclass, field
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from roost.llm.errors import LLMRequestError


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Where a provider lives and which setting holds its key."""

    key: str
    base_url: str
    default_headers: dict[str, str] = field(default_factory=dict)


PROVIDERS: dict[str, ProviderSpec] = {
    spec.key: spec
    for spec in (
        ProviderSpec("groq", "https://api.groq.com/openai/v1"),
        ProviderSpec(
            "openrouter",
            "https://openrouter.ai/api/v1",
            # OpenRouter uses these to attribute traffic to an app; both are optional.
            default_headers={
                "HTTP-Referer": "https://github.com/pranitdeshpande98/roost-hr-assistant",
                "X-Title": "Roost HR Assistant",
            },
        ),
    )
}


class LLMSettings(BaseSettings):
    """API keys, read from the environment / .env. Owned by the llm module."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    # The agent's model chain, primary first (ADR-0009). Comma-separated "provider:model".
    agent_models: str = (
        "groq:openai/gpt-oss-120b,"
        "groq:qwen/qwen3.8-27b,"
        "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free"
    )

    @property
    def agent_model_refs(self) -> list[str]:
        """`agent_models` split into individual "provider:model" refs."""
        return [ref.strip() for ref in self.agent_models.split(",") if ref.strip()]

    def api_key_for(self, provider: str) -> SecretStr:
        """The key for `provider`, or a clear error naming the variable to set."""
        key: SecretStr | None = getattr(self, f"{provider}_api_key", None)
        if key is None or not key.get_secret_value():
            raise LLMRequestError(f"Set {provider.upper()}_API_KEY in .env to use {provider}.")
        return key


def parse_model_ref(ref: str) -> tuple[str, str]:
    """Split "provider:model" on the FIRST colon (model ids may contain ":free")."""
    provider, sep, model = ref.strip().partition(":")
    if not sep or not model or provider not in PROVIDERS:
        raise LLMRequestError(
            f"bad model ref {ref!r}; expected 'provider:model' with provider in {sorted(PROVIDERS)}"
        )
    return provider, model


@lru_cache
def get_llm_settings() -> LLMSettings:
    """Settings loaded once per process."""
    return LLMSettings()
