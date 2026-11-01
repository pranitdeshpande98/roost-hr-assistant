"""Vacation request rules V-01 to V-09 (requirements doc), in plain code with no LLM.

Pure functions over plain data: the caller loads the facts (Step 7) and the agent turns
violations into words (Step 8). Nothing here touches the database or reveals balances.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

from roost.core.enums import DayPortion, PayrollClass
from roost.leave.holidays import holiday_on

INTRODUCTORY_DAYS = 90  # handbook 6.2
MIN_NOTICE_DAYS = 14  # handbook 6.4
MAX_CONSECUTIVE_WORKDAYS = 10  # handbook 6.7
TEAM_LIMIT_PERCENT = 15  # handbook 6.6
# Business-rule decision: a team may always have at least this many people off, so a
# very small team (e.g. 2 people, where 15% is 0.3) is not blocked from all vacation.
MIN_PEOPLE_OFF_PER_TEAM = 1
MAX_DAYS_PER_REQUEST = 31  # sanity bound on input, not a policy rule


class RuleId(StrEnum):
    """Requirement IDs, so a failure can be traced to a requirement and a handbook section."""

    INTRODUCTORY_PERIOD = "V-01"
    NOTICE = "V-02"
    SCHEDULED_SHIFT = "V-03"
    FUTURE_AND_NO_OVERLAP = "V-04"
    PRODUCTION_BLACKOUT = "V-05"
    TEAM_LIMIT = "V-06"
    CONSECUTIVE_DAYS = "V-07"
    ENOUGH_VACATION = "V-08"


HANDBOOK_SECTION = {
    RuleId.INTRODUCTORY_PERIOD: "6.2",
    RuleId.NOTICE: "6.4",
    RuleId.SCHEDULED_SHIFT: "6.3",
    RuleId.FUTURE_AND_NO_OVERLAP: "7.1",
    RuleId.PRODUCTION_BLACKOUT: "6.5",
    RuleId.TEAM_LIMIT: "6.6",
    RuleId.CONSECUTIVE_DAYS: "6.7",
    RuleId.ENOUGH_VACATION: "6.8",
}


@dataclass(frozen=True, slots=True)
class RequestedDay:
    day: date
    portion: DayPortion = DayPortion.FULL


@dataclass(frozen=True, slots=True)
class VacationRequestDraft:
    """The days an employee asked for, in date order, each date once."""

    days: tuple[RequestedDay, ...]

    def __post_init__(self) -> None:
        if not self.days:
            raise ValueError("a request needs at least one day")
        if len(self.days) > MAX_DAYS_PER_REQUEST:
            raise ValueError(f"a request can cover at most {MAX_DAYS_PER_REQUEST} days")
        dates = [d.day for d in self.days]
        if len(set(dates)) != len(dates):
            raise ValueError("each date can appear only once")
        if dates != sorted(dates):
            raise ValueError("days must be in date order")

    @property
    def first_day(self) -> date:
        return self.days[0].day


@dataclass(frozen=True, slots=True)
class EmployeeFacts:
    hire_date: date
    payroll_class: PayrollClass
    workdays: frozenset[int]  # ISO weekdays of the employee's shift: 1 = Monday
    shift_hours: Decimal  # paid hours in one full shift


@dataclass(frozen=True, slots=True)
class LeaveFacts:
    """What the rules need beyond the employee: loaded fresh for every check."""

    available_hours: Decimal  # never shown to the employee (FR-06)
    pending_hours: Decimal  # hours in the employee's other pending requests
    booked_days: frozenset[date]  # days in the employee's other active requests
    team_size: int  # same department, location and shift
    team_off: Mapping[date, int] = field(default_factory=dict)  # others already off, by day


@dataclass(frozen=True, slots=True)
class RuleViolation:
    rule: RuleId
    dates: tuple[date, ...] = ()  # the days that broke the rule, when it is about days

    @property
    def section(self) -> str:
        return HANDBOOK_SECTION[self.rule]


@dataclass(frozen=True, slots=True)
class RuleCheck:
    """The outcome. `charged_hours` is safe to show; balances never appear here."""

    violations: tuple[RuleViolation, ...]
    charged_hours: Decimal
    holidays: tuple[date, ...]  # observed holidays in the request: not charged (V-09)

    @property
    def ok(self) -> bool:
        return not self.violations


def _hours(portion: DayPortion, shift_hours: Decimal) -> Decimal:
    return shift_hours if portion is DayPortion.FULL else shift_hours / 2


def _is_workday(day: date, employee: EmployeeFacts) -> bool:
    return day.isoweekday() in employee.workdays


def _in_blackout(day: date) -> bool:
    return (day.month, day.day) >= (11, 15)  # Nov 15 - Dec 31 (handbook 6.5)


def team_limit(team_size: int) -> int:
    """Most people from one team who may be off on a day (handbook 6.6)."""
    return max(team_size * TEAM_LIMIT_PERCENT // 100, MIN_PEOPLE_OFF_PER_TEAM)


def longest_workday_run(days: Sequence[date], employee: EmployeeFacts) -> int:
    """Most scheduled workdays in a row covered by `days`. Days off and holidays in
    between neither break nor extend a run, so Mon-Fri plus the next Mon-Fri is 10."""
    if not days:
        return 0
    wanted = set(days)
    best = run = 0
    day = min(days)
    while day <= max(days):
        if _is_workday(day, employee) and holiday_on(day) is None:
            run = run + 1 if day in wanted else 0
            best = max(best, run)
        day += timedelta(days=1)
    return best


def check_request(
    draft: VacationRequestDraft,
    employee: EmployeeFacts,
    facts: LeaveFacts,
    *,
    today: date,
) -> RuleCheck:
    """Check a request against V-01 to V-09. Every rule runs, so the employee can fix
    everything at once instead of one error per attempt."""
    violations: list[RuleViolation] = []
    dates = [d.day for d in draft.days]
    holidays = tuple(d for d in dates if holiday_on(d) is not None)
    vacation_days = [d for d in draft.days if d.day not in holidays]  # V-09

    # V-01: vacation is usable once the introductory period is over (6.2).
    if draft.first_day < employee.hire_date + timedelta(days=INTRODUCTORY_DAYS):
        violations.append(RuleViolation(RuleId.INTRODUCTORY_PERIOD))

    # V-02: at least 14 calendar days' notice (6.4).
    if draft.first_day < today + timedelta(days=MIN_NOTICE_DAYS):
        violations.append(RuleViolation(RuleId.NOTICE))

    # V-03: only days the employee is scheduled to work (6.3).
    off_shift = tuple(d for d in dates if not _is_workday(d, employee))
    if off_shift:
        violations.append(RuleViolation(RuleId.SCHEDULED_SHIFT, off_shift))

    # V-04: in the future, and not already in another active request (7.1).
    clashes = tuple(d for d in dates if d <= today or d in facts.booked_days)
    if clashes:
        violations.append(RuleViolation(RuleId.FUTURE_AND_NO_OVERLAP, clashes))

    # V-05: Weekly Production blackout, Nov 15 - Dec 31 (6.5).
    if employee.payroll_class is PayrollClass.WEEKLY_PRODUCTION:
        blacked_out = tuple(d for d in dates if _in_blackout(d))
        if blacked_out:
            violations.append(RuleViolation(RuleId.PRODUCTION_BLACKOUT, blacked_out))

    # V-06: the team stays within its limit on every vacation day (6.6).
    limit = team_limit(facts.team_size)
    full = tuple(d.day for d in vacation_days if facts.team_off.get(d.day, 0) + 1 > limit)
    if full:
        violations.append(RuleViolation(RuleId.TEAM_LIMIT, full))

    # V-07: at most 10 scheduled workdays in a row (6.7).
    if longest_workday_run([d.day for d in vacation_days], employee) > MAX_CONSECUTIVE_WORKDAYS:
        violations.append(RuleViolation(RuleId.CONSECUTIVE_DAYS))

    # V-08: available minus pending covers it. The violation carries no numbers (FR-06).
    charged = sum((_hours(d.portion, employee.shift_hours) for d in vacation_days), Decimal(0))
    if charged > facts.available_hours - facts.pending_hours:
        violations.append(RuleViolation(RuleId.ENOUGH_VACATION))

    return RuleCheck(violations=tuple(violations), charged_hours=charged, holidays=holidays)
