"""Tests for roost.agent.prompts."""

from datetime import date

import pytest

from roost.agent.prompts import system_prompt_for
from roost.agent.state import AgentState
from roost.core.enums import Channel, Language

TODAY = date(2026, 11, 2)


@pytest.mark.parametrize(
    ("language", "name"),
    [(Language.EN, "English"), (Language.ES, "Spanish"), (Language.VI, "Vietnamese")],
)
def test_prompt_names_the_reply_language(language: Language, name: str) -> None:
    prompt = system_prompt_for(AgentState(language=language, channel=Channel.TEAMS), TODAY)

    assert f"Reply in {name}" in prompt


def test_kiosk_and_teams_get_different_styles() -> None:
    kiosk = system_prompt_for(AgentState(language=Language.EN, channel=Channel.KIOSK), TODAY)
    teams = system_prompt_for(AgentState(language=Language.EN, channel=Channel.TEAMS), TODAY)

    assert "kiosk" in kiosk
    assert "Microsoft Teams" in teams


def test_prompt_keeps_the_safety_rules() -> None:
    prompt = system_prompt_for(AgentState(language=Language.ES, channel=Channel.KIOSK), TODAY)

    assert "call\n   search_handbook first" in prompt
    assert "[§<number>]" in prompt
    assert "Never state or guess them" in prompt
    assert "Do not add a disclaimer" in prompt  # code adds it; avoids a double footer
    assert "{" not in prompt  # every placeholder was filled


def test_restricted_questions_get_no_handbook_figures_instead() -> None:
    prompt = system_prompt_for(AgentState(language=Language.ES, channel=Channel.KIOSK), TODAY)

    assert "say you do not have\n   that information and refer them to HR" in prompt
    assert "Do not answer with handbook figures instead" in prompt


def test_prompt_gives_todays_date_and_weekday() -> None:
    prompt = system_prompt_for(AgentState(language=Language.EN, channel=Channel.KIOSK), TODAY)

    assert "Today is 2026-11-02 (Monday)" in prompt  # so "next Friday" becomes a real date


def test_booking_goes_straight_to_the_vacation_tool() -> None:
    prompt = system_prompt_for(AgentState(language=Language.VI, channel=Channel.KIOSK), TODAY)

    # Rule 1 (search first) must not apply to booking, or the model answers from the
    # handbook instead of letting the tool check the request.
    assert "Exception: when the\n   employee asks for specific days off, follow rule 7" in prompt
    assert "call\n   request_vacation right away" in prompt
    assert "do not ask your own confirmation question" in prompt
    assert '"cả ngày" all mean full' in prompt


def test_prompt_forbids_citations_and_rules_the_tools_did_not_return() -> None:
    prompt = system_prompt_for(AgentState(language=Language.EN, channel=Channel.KIOSK), TODAY)

    assert "Cite only section\n   numbers that appear in tool results" in prompt
    assert "never add a rule, condition or exception" in prompt


def test_prompt_sends_payday_questions_to_the_tool_and_forbids_amounts() -> None:
    prompt = system_prompt_for(AgentState(language=Language.EN, channel=Channel.KIOSK), TODAY)

    assert "call my_pay_schedule" in prompt
    assert "Never state, estimate or calculate any pay amount" in prompt
