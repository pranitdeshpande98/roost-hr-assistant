"""The pause-for-confirmation flow through the real graph, with fakes for model and tool."""

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from roost.agent.confirmation import Confirmation
from roost.agent.conversation import ask
from roost.agent.graph import build_graph
from roost.agent.messages import default_messages
from roost.agent.tools import ToolContext, ToolRegistry
from roost.core.enums import Channel, Language
from roost.llm.fake import ScriptedChatModel, text_reply, tool_reply
from roost.llm.types import ToolCall, ToolSpec

MESSAGES = default_messages()


class SubmitTool:
    """Asks to confirm, and 'saves' only on yes. Counts runs to show the resume re-run."""

    def __init__(self) -> None:
        self.runs = 0
        self.saved = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="submit", description="Submit.", parameters={"type": "object"})

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        self.runs += 1
        reply = context.confirm("Submit Monday? Reply YES or NO.")
        if reply.answer is Confirmation.YES:
            self.saved += 1
            return "SUBMITTED. Reference VR-7K2M9Q."
        return f"NOT SUBMITTED: {reply.answer.value} ({reply.text})"


def _agent(model: ScriptedChatModel, tool: SubmitTool) -> Any:
    return build_graph(
        model,
        ToolRegistry([tool]),
        system_prompt=lambda state, today: "sys",
        messages=MESSAGES,
        checkpointer=InMemorySaver(),
    )


def _call() -> ToolCall:
    return ToolCall(id="c1", name="submit", arguments={})


EN: dict[str, Any] = {"language": Language.EN, "channel": Channel.KIOSK}


def test_turn_pauses_with_the_question_and_saves_nothing() -> None:
    tool = SubmitTool()
    agent = _agent(ScriptedChatModel([tool_reply(_call())]), tool)

    reply = ask(agent, "t1", "Book Monday off", **EN)

    assert reply.startswith("Submit Monday? Reply YES or NO.")
    assert reply.endswith(MESSAGES.get("disclaimer", Language.EN))  # FR-04 even here
    assert tool.saved == 0


def test_yes_resumes_and_saves_once() -> None:
    tool = SubmitTool()
    model = ScriptedChatModel([tool_reply(_call()), text_reply("Done: VR-7K2M9Q.")])
    agent = _agent(model, tool)

    ask(agent, "t1", "Book Monday off", **EN)
    reply = ask(agent, "t1", "yes", **EN)

    assert reply.startswith("Done: VR-7K2M9Q.")
    assert tool.saved == 1
    assert tool.runs == 2  # the tool re-ran from the top on resume: why it must ask first
    tool_result = model.requests[1].messages[-1]
    assert tool_result.content == "SUBMITTED. Reference VR-7K2M9Q."


def test_no_resumes_without_saving() -> None:
    tool = SubmitTool()
    model = ScriptedChatModel([tool_reply(_call()), text_reply("OK, not submitted.")])
    agent = _agent(model, tool)

    ask(agent, "t1", "Book Monday off", **EN)
    ask(agent, "t1", "no", **EN)

    assert tool.saved == 0
    assert model.requests[1].messages[-1].content == "NOT SUBMITTED: no (no)"


def test_unclear_reply_is_passed_to_the_model_not_taken_as_yes() -> None:
    tool = SubmitTool()
    model = ScriptedChatModel([tool_reply(_call()), text_reply("Let's fix Friday first.")])
    agent = _agent(model, tool)

    ask(agent, "t1", "Book Monday off", **EN)
    ask(agent, "t1", "yes but make Friday a half day", **EN)

    assert tool.saved == 0
    assert "make Friday a half day" in (model.requests[1].messages[-1].content or "")


def test_after_confirming_the_next_message_is_a_new_question() -> None:
    tool = SubmitTool()
    model = ScriptedChatModel(
        [tool_reply(_call()), text_reply("Done."), text_reply("Ten days a year.")]
    )
    agent = _agent(model, tool)

    ask(agent, "t1", "Book Monday off", **EN)
    ask(agent, "t1", "yes", **EN)
    reply = ask(agent, "t1", "How many vacation days do I get?", **EN)

    assert reply.startswith("Ten days a year.")
    assert model.requests[2].messages[-1].content == "How many vacation days do I get?"
