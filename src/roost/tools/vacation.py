"""Vacation tools: request, change, cancel and list. Rules run in code (roost.leave.rules);
nothing is saved until the employee explicitly confirms (FR-10)."""

import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from roost.agent.confirmation import Confirmation, ConfirmationReply
from roost.agent.messages import MessageCatalog
from roost.agent.tools import Tool, ToolContext
from roost.core.enums import DayPortion, Language
from roost.leave.repository import (
    RequestNotChangeableError,
    RequestNotFoundError,
    RuleViolationError,
    cancel_request,
    change_request,
    check_draft,
    create_request,
    ensure_changeable,
    get_request,
    list_requests,
)
from roost.leave.rules import RequestedDay, RuleCheck, RuleId, VacationRequestDraft
from roost.llm.types import ToolSpec

Sessions = Callable[[], AbstractContextManager[Session]]

# Plain-language reasons (FR-11), given to the model to explain in the employee's language.
# None of them mentions a balance or hours remaining (FR-06).
RULE_EXPLANATIONS = {
    RuleId.INTRODUCTORY_PERIOD: "Vacation can be used only after the 90-day introductory period.",
    RuleId.NOTICE: (
        "The first day off must be at least 14 calendar days from today. With less notice, "
        "the employee should talk to their supervisor directly."
    ),
    RuleId.SCHEDULED_SHIFT: "Only days the employee is scheduled to work can be requested.",
    RuleId.FUTURE_AND_NO_OVERLAP: "These days are past or already in another of their requests.",
    RuleId.PRODUCTION_BLACKOUT: (
        "Weekly Production employees cannot take vacation from November 15 to December 31."
    ),
    RuleId.TEAM_LIMIT: "Their team already has the most people allowed off. Choose other dates.",
    RuleId.CONSECUTIVE_DAYS: (
        "At most 10 scheduled workdays in a row; longer periods need the employee's HR team."
    ),
    RuleId.ENOUGH_VACATION: (
        "There is not enough available vacation time. Their HR team can confirm their hours."
    ),
}

NOT_SIGNED_IN = (
    "Error: the employee is not signed in. Ask them to sign in at the kiosk with their "
    "employee number and PIN, or in Teams, before handling vacation."
)

_DAYS_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "maxItems": 31,
    "items": {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "portion": {"type": "string", "enum": ["full", "half"]},
        },
        "required": ["date", "portion"],
    },
}
_REFERENCE_SCHEMA = {"type": "string", "description": "Request reference, e.g. VR-7K2M9Q"}


class DayArgument(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    day: date = Field(alias="date")
    portion: DayPortion = DayPortion.FULL


class DaysArguments(BaseModel):
    days: list[DayArgument] = Field(min_length=1, max_length=31)

    def draft(self) -> VacationRequestDraft:
        ordered = sorted(self.days, key=lambda d: d.day)
        return VacationRequestDraft(tuple(RequestedDay(d.day, d.portion) for d in ordered))


class ChangeArguments(DaysArguments):
    reference_code: str = Field(min_length=9, max_length=12)


class ReferenceArguments(BaseModel):
    reference_code: str = Field(min_length=9, max_length=12)


def explain(check: RuleCheck) -> str:
    """Rule failures as text for the model, each with its handbook section and dates."""
    lines = []
    for violation in check.violations:
        dates = (
            f" Dates: {', '.join(d.isoformat() for d in violation.dates)}."
            if violation.dates
            else ""
        )
        lines.append(f"- [§{violation.section}] {RULE_EXPLANATIONS[violation.rule]}{dates}")
    return (
        "NOT SUBMITTED. The request breaks these rules. Explain them to the employee in "
        "their language and cite the sections:\n" + "\n".join(lines)
    )


def _details(draft: VacationRequestDraft, language: Language, messages: MessageCatalog) -> str:
    portion = {
        DayPortion.FULL: messages.get("full_day", language),
        DayPortion.HALF: messages.get("half_day", language),
    }
    return "\n".join(f"- {d.day.isoformat()} ({portion[d.portion]})" for d in draft.days)


def _declined(reply: ConfirmationReply, action: str) -> str:
    if reply.answer is Confirmation.NO:
        return f"NOT {action}: the employee said no."
    return (
        f"NOT {action}: the employee did not clearly confirm. Their reply was: "
        f"{reply.text!r}. Help with what they said, and ask again if they still want it."
    )


def _parse[T: BaseModel](model: type[T], arguments: dict[str, Any]) -> T | str:
    try:
        return model.model_validate(arguments)
    except ValidationError as error:
        fields = sorted({".".join(map(str, e["loc"])) for e in error.errors()})
        return f"Error: invalid arguments ({', '.join(fields)}). Fix them and call again."


class _VacationTool:
    def __init__(self, sessions: Sessions, messages: MessageCatalog) -> None:
        self._sessions = sessions
        self._messages = messages


class RequestVacationTool(_VacationTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="request_vacation",
            description=(
                "Check vacation days against the rules and, if they pass, ask the employee "
                "to confirm and submit the request."
            ),
            parameters={
                "type": "object",
                "properties": {"days": _DAYS_SCHEMA},
                "required": ["days"],
            },
        )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        if context.employee_id is None:
            return NOT_SIGNED_IN
        args = _parse(DaysArguments, arguments)
        if isinstance(args, str):
            return args
        try:
            draft = args.draft()
        except ValueError as error:
            return f"Error: {error}."
        employee: uuid.UUID = context.employee_id

        with self._sessions() as session:  # read-only: safe to repeat when resumed
            check = check_draft(session, employee, draft, today=context.today)
        if not check.ok:
            return explain(check)

        question = self._messages.get("confirm_submit", context.language)
        reply = context.confirm(
            question.format(details=_details(draft, context.language, self._messages))
        )
        if reply.answer is not Confirmation.YES:
            return _declined(reply, "SUBMITTED")

        with self._sessions() as session:
            try:
                request = create_request(session, employee, draft, today=context.today)
            except RuleViolationError as error:  # someone took the spot meanwhile
                return explain(error.check)
            session.commit()
            code = request.reference_code
        return (
            f"SUBMITTED. Reference {code}. Status: pending supervisor and HR approval. "
            "Give the employee the reference."
        )


class ChangeVacationTool(_VacationTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="change_vacation_request",
            description=(
                "Replace the days of one of the employee's requests that has not started. "
                "Asks the employee to confirm. Both approvals start again."
            ),
            parameters={
                "type": "object",
                "properties": {"reference_code": _REFERENCE_SCHEMA, "days": _DAYS_SCHEMA},
                "required": ["reference_code", "days"],
            },
        )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        if context.employee_id is None:
            return NOT_SIGNED_IN
        args = _parse(ChangeArguments, arguments)
        if isinstance(args, str):
            return args
        try:
            draft = args.draft()
        except ValueError as error:
            return f"Error: {error}."
        employee: uuid.UUID = context.employee_id

        with self._sessions() as session:
            try:
                request = get_request(session, employee, args.reference_code)
                ensure_changeable(request, context.today)
            except RequestNotFoundError:
                return f"Error: no request {args.reference_code} for this employee."
            except RequestNotChangeableError:
                return "Error: that request has started or is no longer active."
            code = request.reference_code
            check = check_draft(
                session, employee, draft, today=context.today, ignore_request_id=request.id
            )
        if not check.ok:
            return explain(check)

        question = self._messages.get("confirm_change", context.language).format(
            reference=code, details=_details(draft, context.language, self._messages)
        )
        reply = context.confirm(question)
        if reply.answer is not Confirmation.YES:
            return _declined(reply, "CHANGED")

        with self._sessions() as session:
            try:
                change_request(session, employee, code, draft, today=context.today)
            except RuleViolationError as error:
                return explain(error.check)
            session.commit()
        return f"CHANGED. {code} now has the new dates and is pending both approvals again."


class CancelVacationTool(_VacationTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="cancel_vacation_request",
            description="Cancel one of the employee's requests before its first day. Asks to confirm.",
            parameters={
                "type": "object",
                "properties": {"reference_code": _REFERENCE_SCHEMA},
                "required": ["reference_code"],
            },
        )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:
        if context.employee_id is None:
            return NOT_SIGNED_IN
        args = _parse(ReferenceArguments, arguments)
        if isinstance(args, str):
            return args
        employee: uuid.UUID = context.employee_id

        with self._sessions() as session:
            try:
                request = get_request(session, employee, args.reference_code)
                ensure_changeable(request, context.today)
            except RequestNotFoundError:
                return f"Error: no request {args.reference_code} for this employee."
            except RequestNotChangeableError:
                return "Error: that request has started or is no longer active."
            code = request.reference_code

        question = self._messages.get("confirm_cancel", context.language).format(reference=code)
        reply = context.confirm(question)
        if reply.answer is not Confirmation.YES:
            return _declined(reply, "CANCELLED")

        with self._sessions() as session:
            cancel_request(session, employee, code, today=context.today)
            session.commit()
        return f"CANCELLED. {code} is cancelled and the approvers will be notified."


class ListVacationTool(_VacationTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="list_my_vacation_requests",
            description="List the employee's vacation requests with their status and dates.",
            parameters={"type": "object", "properties": {}},
        )

    def run(self, arguments: dict[str, Any], context: ToolContext) -> str:  # noqa: ARG002 - takes no arguments
        if context.employee_id is None:
            return NOT_SIGNED_IN
        with self._sessions() as session:
            requests = list_requests(session, context.employee_id)
            lines = [
                _summary(r.reference_code, r.status.value, [d.day for d in r.days])
                for r in requests
            ]
        return "\n".join(lines) if lines else "The employee has no vacation requests."


def _summary(code: str, status: str, days: Sequence[date]) -> str:
    span = (
        days[0].isoformat()
        if len(days) == 1
        else f"{days[0].isoformat()} to {days[-1].isoformat()}"
    )
    return f"{code}: {status}, {span} ({len(days)} day{'s' if len(days) != 1 else ''})"


def vacation_tools(sessions: Sessions, messages: MessageCatalog) -> list[Tool]:
    """All four vacation tools, sharing one session factory and message catalog."""
    return [
        RequestVacationTool(sessions, messages),
        ChangeVacationTool(sessions, messages),
        CancelVacationTool(sessions, messages),
        ListVacationTool(sessions, messages),
    ]
