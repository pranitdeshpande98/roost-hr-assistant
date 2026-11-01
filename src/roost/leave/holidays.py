"""Company holidays and their observed dates (handbook Section 12)."""

from dataclasses import dataclass
from datetime import date, timedelta

MONDAY, THURSDAY, SATURDAY, SUNDAY = 0, 3, 5, 6


@dataclass(frozen=True, slots=True)
class Holiday:
    """A company holiday: its calendar date and the date it is observed."""

    name: str
    actual_date: date
    observed_date: date


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th given weekday of a month, e.g. the 4th Thursday of November."""
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last given weekday of a month, e.g. the last Monday of May."""
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    return last_day - timedelta(days=(last_day.weekday() - weekday) % 7)


def observed_date(actual: date) -> date:
    """Saturday holidays move to Friday; Sunday holidays move to Monday (Section 12.2)."""
    if actual.weekday() == SATURDAY:
        return actual - timedelta(days=1)
    if actual.weekday() == SUNDAY:
        return actual + timedelta(days=1)
    return actual


def company_holidays(year: int) -> list[Holiday]:
    """The six company holidays for a calendar year, in date order (Section 12.1).

    An observed date can fall in the previous year: when January 1 is a
    Saturday, New Year's Day is observed on December 31.
    """
    actual_dates = [
        ("New Year's Day", date(year, 1, 1)),
        ("Memorial Day", _last_weekday(year, 5, MONDAY)),
        ("Independence Day", date(year, 7, 4)),
        ("Labor Day", _nth_weekday(year, 9, MONDAY, 1)),
        ("Thanksgiving Day", _nth_weekday(year, 11, THURSDAY, 4)),
        ("Christmas Day", date(year, 12, 25)),
    ]
    return [Holiday(name, actual, observed_date(actual)) for name, actual in actual_dates]


def holiday_on(day: date) -> Holiday | None:
    """The holiday observed on a given day, if any.

    Checks the next year too, because next year's New Year's Day can be
    observed on December 31 of this year.
    """
    for year in (day.year, day.year + 1):
        for holiday in company_holidays(year):
            if holiday.observed_date == day:
                return holiday
    return None
