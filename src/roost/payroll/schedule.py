"""Paydays and pay periods per payroll class (handbook Section 5), in plain code.

Pure functions over dates: no database, no model, no clock. Nothing here knows or
returns any amount of money (FR-06).
"""

from dataclasses import dataclass
from datetime import date, timedelta

from roost.core.enums import PayrollClass
from roost.leave.holidays import holiday_on

FRIDAY = 4  # date.weekday()
# Business rules, decision 11: biweekly paydays are every second Friday from this date.
BIWEEKLY_ANCHOR = date(2026, 1, 9)

WEEKLY_CLASSES = frozenset(
    {PayrollClass.WEEKLY_IT, PayrollClass.WEEKLY_PRODUCTION, PayrollClass.DRIVER}
)
BIWEEKLY_CLASSES = frozenset({PayrollClass.EXECUTIVE, PayrollClass.BIWEEKLY})


@dataclass(frozen=True, slots=True)
class Payday:
    scheduled: date  # the Friday on the schedule
    paid_on: date  # moved earlier when the Friday is a company holiday (handbook 5.2)
    period_start: date  # first day of the pay period it pays (a Sunday)
    period_end: date  # last day of the pay period (a Saturday)
    holiday: str | None  # the holiday that moved it, if any


def is_weekly(payroll_class: PayrollClass) -> bool:
    return payroll_class in WEEKLY_CLASSES


def _business_day_before(day: date) -> date:
    """The closest earlier weekday that is not a company holiday."""
    day -= timedelta(days=1)
    while day.weekday() >= 5 or holiday_on(day) is not None:
        day -= timedelta(days=1)
    return day


def _first_scheduled_on_or_after(payroll_class: PayrollClass, day: date) -> date:
    friday = day + timedelta(days=(FRIDAY - day.weekday()) % 7)
    if is_weekly(payroll_class):
        return friday
    weeks_off = ((friday - BIWEEKLY_ANCHOR).days // 7) % 2
    return friday + timedelta(days=7 * weeks_off)


def payday_for(payroll_class: PayrollClass, scheduled: date) -> Payday:
    """The Payday for a scheduled Friday: when it is paid and which period it covers.

    The period ends on the Saturday six days before payday ("the Friday after the week
    ends", handbook 5.1); a biweekly period is the two weeks ending on that Saturday.
    """
    period_end = scheduled - timedelta(days=6)
    weeks = 1 if is_weekly(payroll_class) else 2
    holiday = holiday_on(scheduled)
    return Payday(
        scheduled=scheduled,
        paid_on=_business_day_before(scheduled) if holiday else scheduled,
        period_start=period_end - timedelta(days=7 * weeks - 1),
        period_end=period_end,
        holiday=holiday.name if holiday else None,
    )


def upcoming_paydays(payroll_class: PayrollClass, today: date, count: int = 3) -> list[Payday]:
    """The next `count` paydays not yet paid as of `today` (today's counts)."""
    if count < 1:
        raise ValueError("count must be at least 1")
    step = timedelta(days=7 if is_weekly(payroll_class) else 14)
    # Start a week back: a holiday can move a scheduled Friday's payment to before today.
    scheduled = _first_scheduled_on_or_after(payroll_class, today - timedelta(days=7))
    paydays: list[Payday] = []
    while len(paydays) < count:
        payday = payday_for(payroll_class, scheduled)
        if payday.paid_on >= today:
            paydays.append(payday)
        scheduled += step
    return paydays
