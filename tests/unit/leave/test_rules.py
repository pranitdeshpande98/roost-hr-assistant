"""Tests for roost.leave.rules: every rule, its boundary, and how rules combine."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from roost.core.enums import DayPortion, PayrollClass
from roost.leave.rules import (
    HANDBOOK_SECTION,
    EmployeeFacts,
    LeaveFacts,
    RequestedDay,
    RuleCheck,
    RuleId,
    RuleViolation,
    VacationRequestDraft,
    check_request,
    longest_workday_run,
    team_limit,
)

TODAY = date(2026, 11, 2)  # a Monday
MON_TO_FRI = frozenset({1, 2, 3, 4, 5})
OFFICE = EmployeeFacts(
    hire_date=date(2020, 1, 6),
    payroll_class=PayrollClass.BIWEEKLY,
    workdays=MON_TO_FRI,
    shift_hours=Decimal(8),
)
PLENTY = LeaveFacts(
    available_hours=Decimal(80),
    pending_hours=Decimal(0),
    booked_days=frozenset(),
    team_size=20,  # limit: 3 people off per day
)
MEMORIAL_DAY_2027 = date(2027, 5, 31)  # a Monday


def _draft(*days: date, portion: DayPortion = DayPortion.FULL) -> VacationRequestDraft:
    return VacationRequestDraft(tuple(RequestedDay(d, portion) for d in days))


def _range(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _weekdays(first: date, last: date) -> list[date]:
    return [d for d in _range(first, last) if d.isoweekday() <= 5]


def _rules(result: RuleCheck) -> set[RuleId]:
    return {v.rule for v in result.violations}


def _violation(result: RuleCheck, rule: RuleId) -> RuleViolation:
    return next(v for v in result.violations if v.rule is rule)


def _check(
    *days: date,
    employee: EmployeeFacts = OFFICE,
    facts: LeaveFacts = PLENTY,
    portion: DayPortion = DayPortion.FULL,
) -> RuleCheck:
    return check_request(_draft(*days, portion=portion), employee, facts, today=TODAY)


# --- the happy path ----------------------------------------------------------------


def test_a_normal_week_off_passes_and_charges_five_shifts() -> None:
    result = check_request(
        _draft(*_weekdays(date(2027, 1, 11), date(2027, 1, 15))), OFFICE, PLENTY, today=TODAY
    )

    assert result.ok
    assert result.charged_hours == Decimal(40)
    assert result.holidays == ()


@pytest.mark.parametrize(
    ("shift", "half_day"), [(Decimal(8), Decimal(4)), (Decimal(10), Decimal(5))]
)
def test_half_day_is_half_the_scheduled_shift(shift: Decimal, half_day: Decimal) -> None:
    driver_or_office = replace(OFFICE, shift_hours=shift)

    result = check_request(
        _draft(date(2027, 1, 11), portion=DayPortion.HALF), driver_or_office, PLENTY, today=TODAY
    )

    assert result.ok
    assert result.charged_hours == half_day


# --- V-01 introductory period ------------------------------------------------------


def test_v01_vacation_before_the_introductory_period_ends_fails() -> None:
    new_hire = replace(OFFICE, hire_date=date(2026, 12, 1))  # period ends 2027-03-01

    assert RuleId.INTRODUCTORY_PERIOD in _rules(_check(date(2027, 2, 26), employee=new_hire))


def test_v01_the_first_day_after_the_period_is_allowed() -> None:
    new_hire = replace(OFFICE, hire_date=date(2026, 12, 1))

    assert RuleId.INTRODUCTORY_PERIOD not in _rules(_check(date(2027, 3, 1), employee=new_hire))


# --- V-02 notice -------------------------------------------------------------------


@pytest.mark.parametrize(("days_ahead", "fails"), [(13, True), (14, False), (30, False)])
def test_v02_needs_fourteen_calendar_days_notice(days_ahead: int, fails: bool) -> None:
    every_day = replace(OFFICE, workdays=frozenset(range(1, 8)))  # keeps V-03 out of the way

    result = _check(TODAY + timedelta(days=days_ahead), employee=every_day)

    assert (RuleId.NOTICE in _rules(result)) is fails


# --- V-03 scheduled shift ----------------------------------------------------------


def test_v03_days_off_shift_are_listed() -> None:
    saturday, sunday = date(2027, 1, 16), date(2027, 1, 17)

    result = _check(date(2027, 1, 15), saturday, sunday)

    assert _violation(result, RuleId.SCHEDULED_SHIFT).dates == (saturday, sunday)


def test_v03_a_weekend_shift_worker_can_take_a_saturday() -> None:
    weekend_crew = replace(OFFICE, workdays=frozenset({5, 6, 7}))

    assert _check(date(2027, 1, 16), employee=weekend_crew).ok


# --- V-04 future and no overlap ----------------------------------------------------


def test_v04_a_day_already_in_another_request_fails() -> None:
    booked = replace(PLENTY, booked_days=frozenset({date(2027, 1, 12)}))

    result = _check(date(2027, 1, 11), date(2027, 1, 12), facts=booked)

    assert _violation(result, RuleId.FUTURE_AND_NO_OVERLAP).dates == (date(2027, 1, 12),)


def test_v04_today_or_the_past_fails() -> None:
    assert RuleId.FUTURE_AND_NO_OVERLAP in _rules(_check(TODAY))


# --- V-05 Weekly Production blackout ---------------------------------------------


PRODUCTION = replace(OFFICE, payroll_class=PayrollClass.WEEKLY_PRODUCTION)


@pytest.mark.parametrize(
    ("day", "fails"),
    [
        (date(2027, 11, 12), False),  # Friday before the blackout
        (date(2027, 11, 15), True),  # first day
        (date(2027, 12, 31), True),  # last day
        (date(2028, 1, 3), False),  # after it
    ],
)
def test_v05_blackout_runs_november_15_to_december_31(day: date, fails: bool) -> None:
    assert (RuleId.PRODUCTION_BLACKOUT in _rules(_check(day, employee=PRODUCTION))) is fails


def test_v05_blackout_only_applies_to_weekly_production() -> None:
    assert RuleId.PRODUCTION_BLACKOUT not in _rules(_check(date(2027, 12, 1)))


# --- V-06 team limit ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("team_size", "limit"),
    [(2, 1), (6, 1), (7, 1), (20, 3), (58, 8), (203, 30)],
)
def test_team_limit_is_fifteen_percent_but_never_zero(team_size: int, limit: int) -> None:
    assert team_limit(team_size) == limit


def test_v06_a_full_day_is_reported_and_others_pass() -> None:
    day1, day2 = date(2027, 1, 11), date(2027, 1, 12)
    busy = replace(PLENTY, team_off={day1: 2, day2: 3})  # limit 3: day2 is already full

    result = _check(day1, day2, facts=busy)

    assert _violation(result, RuleId.TEAM_LIMIT).dates == (day2,)


def test_v06_a_two_person_team_can_still_have_one_person_off() -> None:
    tiny = replace(PLENTY, team_size=2)

    assert _check(date(2027, 1, 11), facts=tiny).ok
    full = replace(tiny, team_off={date(2027, 1, 11): 1})
    assert RuleId.TEAM_LIMIT in _rules(_check(date(2027, 1, 11), facts=full))


# --- V-07 days in a row ------------------------------------------------------------


def test_v07_two_full_weeks_is_exactly_the_limit() -> None:
    assert _check(*_weekdays(date(2027, 1, 11), date(2027, 1, 22))).ok


def test_v07_eleven_workdays_in_a_row_fails() -> None:
    days = _weekdays(date(2027, 1, 11), date(2027, 1, 25))  # 11 weekdays

    assert RuleId.CONSECUTIVE_DAYS in _rules(_check(*days))


def test_v07_a_holiday_inside_the_run_is_not_counted() -> None:
    # Mon May 24 - Mon Jun 7 2027: 11 weekdays, but May 31 is Memorial Day, so 10 count.
    days = _weekdays(date(2027, 5, 24), date(2027, 6, 7))

    assert RuleId.CONSECUTIVE_DAYS not in _rules(_check(*days))


def test_longest_run_restarts_after_a_skipped_workday() -> None:
    days = [date(2027, 1, 11), date(2027, 1, 12), date(2027, 1, 14), date(2027, 1, 15)]

    assert longest_workday_run(days, OFFICE) == 2  # Wednesday the 13th breaks it


def test_longest_run_of_nothing_is_zero() -> None:
    assert longest_workday_run([], OFFICE) == 0


# --- V-08 enough vacation ----------------------------------------------------------


@pytest.mark.parametrize(("pending", "fails"), [(Decimal(40), False), (Decimal(41), True)])
def test_v08_pending_requests_count_against_available_hours(pending: Decimal, fails: bool) -> None:
    facts = replace(PLENTY, pending_hours=pending)  # 80 available
    week = _weekdays(date(2027, 1, 11), date(2027, 1, 15))  # 40 hours

    assert (RuleId.ENOUGH_VACATION in _rules(_check(*week, facts=facts))) is fails


def test_v08_violation_reveals_no_numbers() -> None:
    broke = replace(PLENTY, available_hours=Decimal(0))

    violation = _violation(_check(date(2027, 1, 11), facts=broke), RuleId.ENOUGH_VACATION)

    assert violation.dates == ()  # nothing about the balance travels with it (FR-06)


# --- V-09 holidays are not charged ------------------------------------------------


def test_v09_a_holiday_in_the_request_is_not_charged_or_counted() -> None:
    days = _weekdays(date(2027, 5, 31), date(2027, 6, 4))  # Memorial Day + 4 days
    holiday_full = replace(PLENTY, team_off={MEMORIAL_DAY_2027: 99})

    result = check_request(_draft(*days), OFFICE, holiday_full, today=TODAY)

    assert result.ok  # the "full" holiday is ignored: nobody is on vacation that day
    assert result.holidays == (MEMORIAL_DAY_2027,)
    assert result.charged_hours == Decimal(32)


# --- combining rules and validating input -----------------------------------------


def test_every_broken_rule_is_reported_at_once() -> None:
    new_production_hire = replace(PRODUCTION, hire_date=date(2026, 10, 1))  # period ends 2026-12-30
    facts = replace(PLENTY, available_hours=Decimal(0))

    result = _check(date(2026, 11, 20), employee=new_production_hire, facts=facts)

    assert _rules(result) == {
        RuleId.INTRODUCTORY_PERIOD,
        RuleId.PRODUCTION_BLACKOUT,
        RuleId.ENOUGH_VACATION,
    }


def test_every_rule_points_to_a_handbook_section() -> None:
    assert set(HANDBOOK_SECTION) == set(RuleId)


@pytest.mark.parametrize(
    ("days", "message"),
    [
        ((), "at least one day"),
        ((date(2027, 1, 11), date(2027, 1, 11)), "only once"),
        ((date(2027, 1, 12), date(2027, 1, 11)), "date order"),
        (tuple(_range(date(2027, 1, 1), date(2027, 2, 1))), "at most 31"),
    ],
)
def test_draft_rejects_malformed_input(days: tuple[date, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _draft(*days)
