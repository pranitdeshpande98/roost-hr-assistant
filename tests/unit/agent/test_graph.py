"""Tests for roost.agent.graph, driven by a scripted model and a fake tool."""

from typing import Any

from roost.agent.graph import build_graph
from roost.agent.messages import default_messages
from roost.agent.state import AgentState
from roost.agent.tools import ToolContext, ToolRegistry
from roost.core.enums import Channel, Language
from roost.llm.errors import LLMUnavailableError
from roost.llm.fake import ScriptedChatModel, text_reply, tool_reply
from roost.llm.types import ChatMessage, ToolCall, ToolSpec

SYSTEM = "You are Roost."
MESSAGES = default_messages()
DISCLAIMER_EN = MESSAGES.get("disclaimer", Language.EN)


class LookupTool:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="lookup", description="Look up.", parameters={"type": "object"})

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        self.calls.append(arguments)
        return "[§6.1] Ten vacation days per year."


def _call(call_id: str = "c1") -> ToolCall:
    return ToolCall(id=call_id, name="lookup", arguments={"q": "vacation"})


def _start(language: Language = Language.EN) -> AgentState:
    return AgentState(
        messages=[ChatMessage(role="user", content="How many vacation days?")],
        language=language,
        channel=Channel.KIOSK,
    )


def _run(
    model: ScriptedChatModel,
    tool: LookupTool,
    *,
    language: Language = Language.EN,
    **kwargs: Any,
) -> dict[str, Any]:
    graph = build_graph(
        model,
        ToolRegistry([tool]),
        system_prompt=lambda state, today: SYSTEM,
        messages=MESSAGES,
        **kwargs,
    )
    return dict(graph.invoke(_start(language)))


def test_direct_answer_gets_the_disclaimer() -> None:
    model = ScriptedChatModel([text_reply("Hello!")])
    tool = LookupTool()

    result = _run(model, tool)

    assert result["reply"] == f"Hello!\n\n{DISCLAIMER_EN}"
    assert tool.calls == []
    assert len(model.requests) == 1


def test_tool_loop_runs_the_tool_then_answers() -> None:
    model = ScriptedChatModel([tool_reply(_call()), text_reply("Ten days [§6.1].")])
    tool = LookupTool()

    result = _run(model, tool)

    roles = [m.role for m in result["messages"]]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert result["messages"][2].tool_call_id == "c1"
    assert result["reply"].startswith("Ten days [§6.1].")
    assert result["tool_rounds"] == 1
    assert tool.calls == [{"q": "vacation"}]


def test_spanish_reply_adds_the_binding_notice() -> None:
    model = ScriptedChatModel([text_reply("Diez días [§6.1].")])

    result = _run(model, LookupTool(), language=Language.ES)

    assert MESSAGES.get("binding_notice", Language.ES) in result["reply"]
    assert result["reply"].endswith(MESSAGES.get("disclaimer", Language.ES))


def test_every_model_call_starts_with_the_system_prompt() -> None:
    model = ScriptedChatModel([tool_reply(_call()), text_reply("done")])

    _run(model, LookupTool())

    for request in model.requests:
        assert request.messages[0] == ChatMessage(role="system", content=SYSTEM)


def test_tools_are_offered_until_the_round_limit_then_withheld() -> None:
    model = ScriptedChatModel(
        [tool_reply(_call("c1")), tool_reply(_call("c2")), text_reply("Final answer.")]
    )
    tool = LookupTool()

    result = _run(model, tool, max_tool_rounds=2)

    offered = [request.tools is not None for request in model.requests]
    assert offered == [True, True, False]  # third call must answer without tools
    assert len(tool.calls) == 2
    assert result["reply"].startswith("Final answer.")


def test_tool_calls_after_the_limit_fall_back_to_the_fixed_message() -> None:
    model = ScriptedChatModel([tool_reply(_call("c1")), tool_reply(_call("c2"))])
    tool = LookupTool()

    result = _run(model, tool, max_tool_rounds=1)

    assert len(tool.calls) == 1  # the second request was ignored
    assert result["reply"].startswith(MESSAGES.get("no_answer", Language.EN))


def test_model_outage_gives_the_fixed_fallback_not_a_crash() -> None:
    model = ScriptedChatModel([LLMUnavailableError("every provider is down")])

    result = _run(model, LookupTool(), language=Language.VI)

    assert result["reply"].startswith(MESSAGES.get("no_answer", Language.VI))
    assert result["reply"].endswith(MESSAGES.get("disclaimer", Language.VI))
