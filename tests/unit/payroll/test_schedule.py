"""Tests for roost.payroll.schedule against real 2026 calendar dates."""

from datetime import date

import pytest

from roost.core.enums import PayrollClass
from roost.payroll.schedule import BIWEEKLY_ANCHOR, payday_for, upcoming_paydays

WEEKLY_CLASSES = [PayrollClass.WEEKLY_PRODUCTION, PayrollClass.WEEKLY_IT, PayrollClass.DRIVER]
BIWEEKLY_CLASSES = [PayrollClass.BIWEEKLY, PayrollClass.EXECUTIVE]


@pytest.mark.parametrize("payroll_class", WEEKLY_CLASSES)
def test_weekly_classes_are_paid_every_friday(payroll_class: PayrollClass) -> None:
    paydays = upcoming_paydays(payroll_class, date(2026, 11, 1), count=3)  # a Sunday

    assert [p.paid_on for p in paydays] == [
        date(2026, 11, 6),
        date(2026, 11, 13),
        date(2026, 11, 20),
    ]


@pytest.mark.parametrize("payroll_class", BIWEEKLY_CLASSES)
def test_biweekly_classes_are_paid_every_other_friday_from_the_anchor(
    payroll_class: PayrollClass,
) -> None:
    paydays = upcoming_paydays(payroll_class, date(2026, 11, 1), count=2)

    assert [p.paid_on for p in paydays] == [date(2026, 11, 13), date(2026, 11, 27)]
    assert all((p.scheduled - BIWEEKLY_ANCHOR).days % 14 == 0 for p in paydays)


def test_the_anchor_itself_is_the_first_biweekly_payday() -> None:
    assert (
        upcoming_paydays(PayrollClass.BIWEEKLY, date(2026, 1, 1), 1)[0].paid_on == BIWEEKLY_ANCHOR
    )


def test_a_payday_today_still_counts_as_next() -> None:
    assert upcoming_paydays(PayrollClass.DRIVER, date(2026, 11, 6), 1)[0].paid_on == date(
        2026, 11, 6
    )


def test_the_day_after_payday_moves_to_the_next_one() -> None:
    assert upcoming_paydays(PayrollClass.DRIVER, date(2026, 11, 7), 1)[0].paid_on == date(
        2026, 11, 13
    )


def test_weekly_period_is_the_sunday_to_saturday_week_before() -> None:
    payday = payday_for(PayrollClass.WEEKLY_PRODUCTION, date(2026, 11, 6))

    assert (payday.period_start, payday.period_end) == (date(2026, 10, 25), date(2026, 10, 31))


def test_biweekly_period_is_the_two_weeks_before() -> None:
    payday = payday_for(PayrollClass.BIWEEKLY, date(2026, 11, 13))

    assert (payday.period_start, payday.period_end) == (date(2026, 10, 25), date(2026, 11, 7))


def test_christmas_friday_moves_payday_to_thursday() -> None:
    payday = upcoming_paydays(PayrollClass.BIWEEKLY, date(2026, 12, 20), 1)[0]

    assert payday.scheduled == date(2026, 12, 25)
    assert payday.paid_on == date(2026, 12, 24)
    assert payday.holiday == "Christmas Day"


def test_observed_independence_day_moves_weekly_payday() -> None:
    # July 4, 2026 is a Saturday, observed on Friday July 3.
    payday = upcoming_paydays(PayrollClass.WEEKLY_PRODUCTION, date(2026, 6, 29), 1)[0]

    assert payday.paid_on == date(2026, 7, 2)
    assert payday.holiday == "Independence Day"


def test_a_moved_payday_earlier_than_today_is_not_skipped_wrongly() -> None:
    # On Thursday Dec 24 the Christmas payday is being paid today.
    payday = upcoming_paydays(PayrollClass.BIWEEKLY, date(2026, 12, 24), 1)[0]

    assert payday.paid_on == date(2026, 12, 24)


def test_dates_before_the_anchor_follow_the_same_rhythm() -> None:
    payday = upcoming_paydays(PayrollClass.EXECUTIVE, date(2025, 12, 20), 1)[0]

    assert payday.scheduled == date(2025, 12, 26)  # two weeks before Jan 9, 2026


def test_count_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        upcoming_paydays(PayrollClass.DRIVER, date(2026, 11, 1), count=0)
