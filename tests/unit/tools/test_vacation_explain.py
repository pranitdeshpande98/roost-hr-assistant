"""Tests for how rule failures are worded for the model (roost.tools.vacation.explain)."""

from datetime import date
from decimal import Decimal

from roost.leave.rules import RuleCheck, RuleId, RuleViolation
from roost.tools.vacation import explain


def test_dates_carry_their_weekday_and_the_section_is_cited() -> None:
    check = RuleCheck(
        violations=(RuleViolation(RuleId.SCHEDULED_SHIFT, (date(2026, 12, 12),)),),
        charged_hours=Decimal(0),
        holidays=(),
    )

    text = explain(check)

    assert text.startswith("NOT SUBMITTED. The request breaks these rules.")
    assert "[§6.3]" in text
    # The model once called 2026-12-12 a Sunday: code states the weekday instead.
    assert "Dates: 2026-12-12 (Saturday)." in text
