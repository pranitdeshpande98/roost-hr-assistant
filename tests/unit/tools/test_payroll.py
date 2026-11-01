"""Tests for roost.tools.payroll that need no database."""

import re
from datetime import date

from roost.agent.confirmation import ConfirmationReply
from roost.agent.tools import ToolContext
from roost.core.enums import Language, PayrollClass
from roost.payroll.schedule import upcoming_paydays
from roost.tools.payroll import PayScheduleTool, describe_schedule
from roost.tools.vacation import NOT_SIGNED_IN


def test_describes_class_frequency_and_dates_with_citations() -> None:
    text = describe_schedule(
        PayrollClass.WEEKLY_PRODUCTION,
        upcoming_paydays(PayrollClass.WEEKLY_PRODUCTION, date(2026, 11, 1)),
    )

    assert text.startswith("Payroll class: Weekly Production (paid every Friday) [§5.1].")
    assert "Next payday: 2026-11-06 (Friday), for the pay period 2026-10-25 to 2026-10-31." in text
    assert text.count("Then:") == 2


def test_holiday_move_is_explained_and_cited() -> None:
    text = describe_schedule(
        PayrollClass.BIWEEKLY, upcoming_paydays(PayrollClass.BIWEEKLY, date(2026, 12, 20))
    )

    assert "2026-12-24 (Thursday)" in text
    assert "moved from 2026-12-25 because of Christmas Day [§5.2]" in text


def test_contains_no_money() -> None:
    text = describe_schedule(
        PayrollClass.EXECUTIVE, upcoming_paydays(PayrollClass.EXECUTIVE, date(2026, 11, 1))
    )

    assert "$" not in text
    assert not re.search(r"\b(salary|wage|rate|deduction)s?\b", text, re.IGNORECASE)


def test_signed_out_employee_is_asked_to_sign_in() -> None:
    context = ToolContext(
        employee_id=None,
        language=Language.EN,
        today=date(2026, 11, 1),
        confirm=ConfirmationReply.from_text,
    )

    def no_database() -> object:
        raise AssertionError("must not touch the database")

    assert PayScheduleTool(no_database).run({}, context) == NOT_SIGNED_IN  # type: ignore[arg-type]
