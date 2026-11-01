"""Smoke-test LLM providers with one real call: a plain reply and a tool call.

Model catalogs change often, so list what is live before picking a model.

Usage:
    uv run python scripts/llm_smoke.py --provider groq --list
    uv run python scripts/llm_smoke.py --provider groq --model openai/gpt-oss-20b
    uv run python scripts/llm_smoke.py --provider openrouter --model openrouter/free
"""

import argparse
import logging
import sys

import openai

from roost.llm.client import OpenAICompatibleChatModel
from roost.llm.errors import LLMError
from roost.llm.providers import PROVIDERS, get_llm_settings
from roost.llm.types import ChatMessage, ToolSpec

logger = logging.getLogger("llm_smoke")

DEFAULT_MODELS = {"groq": "openai/gpt-oss-20b", "openrouter": "openrouter/free"}

SEARCH_TOOL = ToolSpec(
    name="search_handbook",
    description="Search the employee handbook for the section that answers a question.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What to look up."}},
        "required": ["query"],
    },
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test an LLM provider.")
    parser.add_argument("--provider", choices=sorted(PROVIDERS), required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="list live model ids and exit")
    action.add_argument("--model", help="model id to test (default: a known free model)")
    return parser.parse_args(argv)


def list_models(provider: str) -> int:
    spec = PROVIDERS[provider]
    key = get_llm_settings().api_key_for(provider)
    client = openai.OpenAI(base_url=spec.base_url, api_key=key.get_secret_value())
    for model_id in sorted(m.id for m in client.models.list()):
        logger.info("%s", model_id)
    return 0


def smoke(provider: str, model_id: str) -> int:
    model = OpenAICompatibleChatModel.create(provider, model_id)

    text = model.chat(
        [ChatMessage(role="user", content="Reply with exactly one word: ready")],
        max_tokens=200,  # reasoning models spend tokens thinking before they answer
    )
    logger.info(
        "text   %-40s %6.0f ms  %4d+%-4d tokens  -> %r",
        text.model,
        text.latency_ms,
        text.usage.prompt_tokens,
        text.usage.completion_tokens,
        (text.message.content or "").strip()[:60],
    )

    tool = model.chat(
        [
            ChatMessage(
                role="system",
                content="You answer HR questions. Always search the handbook before answering.",
            ),
            ChatMessage(role="user", content="How many vacation days do I get each year?"),
        ],
        tools=[SEARCH_TOOL],
        max_tokens=300,
    )
    calls = [f"{c.name}({c.arguments})" for c in tool.message.tool_calls]
    logger.info(
        "tool   %-40s %6.0f ms  %4d+%-4d tokens  -> %s",
        tool.model,
        tool.latency_ms,
        tool.usage.prompt_tokens,
        tool.usage.completion_tokens,
        calls or "NO TOOL CALL (model answered directly)",
    )
    return 0 if calls else 2


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.list:
            return list_models(args.provider)
        return smoke(args.provider, args.model or DEFAULT_MODELS[args.provider])
    except (LLMError, openai.OpenAIError) as exc:
        logger.error("FAILED: %s", exc)
        return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    logger.setLevel(logging.INFO)
    sys.exit(main())
