"""Unit tests for roost.leave.holidays (handbook Section 12)."""

from datetime import date

import pytest

from roost.leave.holidays import company_holidays, holiday_on, observed_date


def _observed(year: int) -> dict[str, date]:
    return {h.name: h.observed_date for h in company_holidays(year)}


def test_returns_six_holidays_in_date_order() -> None:
    holidays = company_holidays(2026)

    assert [h.name for h in holidays] == [
        "New Year's Day",
        "Memorial Day",
        "Independence Day",
        "Labor Day",
        "Thanksgiving Day",
        "Christmas Day",
    ]
    assert [h.actual_date for h in holidays] == sorted(h.actual_date for h in holidays)


@pytest.mark.parametrize(
    ("year", "name", "expected"),
    [
        (2026, "Memorial Day", date(2026, 5, 25)),
        (2026, "Labor Day", date(2026, 9, 7)),
        (2026, "Thanksgiving Day", date(2026, 11, 26)),
        (2027, "Memorial Day", date(2027, 5, 31)),
        (2027, "Thanksgiving Day", date(2027, 11, 25)),
        (2028, "Labor Day", date(2028, 9, 4)),
    ],
)
def test_floating_holidays(year: int, name: str, expected: date) -> None:
    assert _observed(year)[name] == expected


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (date(2026, 7, 4), date(2026, 7, 3)),  # Saturday -> Friday
        (date(2027, 7, 4), date(2027, 7, 5)),  # Sunday -> Monday
        (date(2027, 12, 25), date(2027, 12, 24)),  # Saturday -> Friday
        (date(2026, 12, 25), date(2026, 12, 25)),  # Friday stays
    ],
)
def test_weekend_holidays_move_to_nearest_weekday(actual: date, expected: date) -> None:
    assert observed_date(actual) == expected


def test_new_year_on_saturday_is_observed_in_previous_year() -> None:
    new_year_2028 = company_holidays(2028)[0]

    assert new_year_2028.actual_date == date(2028, 1, 1)
    assert new_year_2028.observed_date == date(2027, 12, 31)


def test_holiday_on_finds_observed_date() -> None:
    holiday = holiday_on(date(2026, 7, 3))

    assert holiday is not None
    assert holiday.name == "Independence Day"


def test_holiday_on_finds_next_years_new_year_in_december() -> None:
    holiday = holiday_on(date(2027, 12, 31))

    assert holiday is not None
    assert holiday.name == "New Year's Day"


def test_holiday_on_returns_none_for_actual_date_when_moved() -> None:
    # July 4, 2026 is a Saturday; the day off is Friday, July 3.
    assert holiday_on(date(2026, 7, 4)) is None


def test_holiday_on_returns_none_for_ordinary_day() -> None:
    assert holiday_on(date(2026, 3, 10)) is None
