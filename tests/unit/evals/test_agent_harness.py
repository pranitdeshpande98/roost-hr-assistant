"""Tests for roost.evals.agent_harness with fakes (no database, no real model)."""

import uuid
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from roost.agent.confirmation import Confirmation
from roost.agent.graph import build_graph
from roost.agent.messages import default_messages
from roost.agent.tools import ToolContext, ToolRegistry
from roost.core.enums import Language
from roost.evals.agent_dataset import AgentCase, Scenario
from roost.evals.agent_harness import (
    AgentCaseRecord,
    ModelOutageError,
    RecordingChatModel,
    append_case_record,
    load_case_records,
    results_path,
    run_case,
)
from roost.llm.errors import LLMUnavailableError
from roost.llm.fake import ScriptedChatModel, text_reply, tool_reply
from roost.llm.types import ToolCall, ToolSpec

SAY = {"en": "Book Monday", "es": "Reserva lunes", "vi": "Đặt thứ Hai"}
YES = {"en": "yes", "es": "sí", "vi": "có"}


class SearchTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="search_handbook", description="d", parameters={"type": "object"})

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        return "[§6.4] Vacation > Notice\nTwo weeks.\n\n[§6.1] Vacation > Amount\nTen days."


class BookTool:
    def __init__(self) -> None:
        self.employee: object = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="request_vacation", description="d", parameters={"type": "object"})

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        self.employee = context.employee_id
        reply = context.confirm("Confirm? YES/NO")
        return "SUBMITTED. Reference VR-7K2M9Q." if reply.answer is Confirmation.YES else "NO"


def _case(*turns: dict[str, str]) -> AgentCase:
    scenario = Scenario.model_validate(
        {
            "id": "s",
            "category": "vacation",
            "employee": "office_biweekly",
            "turns": [{"say": t} for t in turns],
        }
    )
    return AgentCase(case_id="s-en", scenario=scenario, language=Language.EN)


def _make_agent(model: RecordingChatModel, *tools: Any) -> Any:
    def make(sessions: Any) -> Any:
        return build_graph(
            model,
            ToolRegistry(list(tools)),
            system_prompt=lambda state, today: "sys",
            messages=default_messages(),
            checkpointer=InMemorySaver(),
        )

    return make


def _no_sessions() -> Any:
    raise AssertionError("fake tools never open a session")


def test_records_tool_calls_results_and_the_pause() -> None:
    book = BookTool()
    call = ToolCall(id="c1", name="request_vacation", arguments={"days": ["x"]})
    model = RecordingChatModel(
        ScriptedChatModel([tool_reply(call), text_reply("Booked VR-7K2M9Q.", model="fake/m")])
    )

    employee = uuid.uuid4()

    record = run_case(_case(SAY, YES), _make_agent(model, book), model, _no_sessions, employee)

    first, second = record.turns
    assert first.paused is True
    assert first.reply.startswith("Confirm? YES/NO")
    assert first.tool_calls[0].name == "request_vacation"
    assert first.tool_calls[0].arguments == {"days": ["x"]}
    assert first.tool_calls[0].result == ""  # waiting for the employee's answer
    assert second.paused is False
    assert second.tool_calls[0].result == "SUBMITTED. Reference VR-7K2M9Q."
    assert second.reply.startswith("Booked VR-7K2M9Q.")
    assert book.employee == employee  # the signed-in employee reached the tool
    assert second.models == ("fake/m",)


def test_records_which_sections_the_search_returned() -> None:
    call = ToolCall(id="c1", name="search_handbook", arguments={"query": "notice"})
    model = RecordingChatModel(ScriptedChatModel([tool_reply(call), text_reply("14 days.")]))

    record = run_case(_case(SAY), _make_agent(model, SearchTool()), model, _no_sessions, None)

    assert record.turns[0].retrieved_sections == ("6.4", "6.1")


def test_an_outage_stops_the_case_instead_of_scoring_the_fallback() -> None:
    model = RecordingChatModel(ScriptedChatModel([LLMUnavailableError("429")]))

    with pytest.raises(ModelOutageError, match="429"):
        run_case(_case(SAY), _make_agent(model), model, _no_sessions, None)


def test_token_counts_are_summed_over_the_case() -> None:
    model = RecordingChatModel(ScriptedChatModel([text_reply("hi")]))

    record = run_case(_case(SAY), _make_agent(model), model, _no_sessions, None)

    assert (record.prompt_tokens, record.completion_tokens) == (0, 0)  # fakes report none
    assert record.latency_ms >= 0


def test_records_round_trip_with_lf_line_endings(tmp_path: Path) -> None:
    model = RecordingChatModel(ScriptedChatModel([text_reply("hi")]))
    record = run_case(_case(SAY), _make_agent(model), model, _no_sessions, None)
    path = tmp_path / "out" / "r.jsonl"

    append_case_record(path, record)

    assert load_case_records(path) == [record]
    assert b"\r" not in path.read_bytes()
    assert load_case_records(tmp_path / "missing.jsonl") == []


def test_results_path_is_safe_for_a_chain_name(tmp_path: Path) -> None:
    path = results_path(tmp_path, "groq/openai/gpt-oss-120b -> openrouter/x:free")

    assert path.name == "groq_openai_gpt-oss-120b__then__openrouter_x_free.jsonl"


def test_record_types_are_plain_json() -> None:
    record = AgentCaseRecord(
        case_id="c",
        scenario_id="s",
        category="handbook",
        language="en",
        turns=(),
        latency_ms=1.0,
        prompt_tokens=1,
        completion_tokens=1,
    )

    assert AgentCaseRecord.model_validate_json(record.model_dump_json()) == record
